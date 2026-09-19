import json
import re
from typing import Any

from app.ingest.common import NCT_PATTERN, empty_study, integer, pmid

NORMALIZER_VERSION = "openalex-v3-snapshot-metadata"
REVIEW_TITLE_RE = re.compile(
    r"\b(?:(?:systematic|scoping|narrative|umbrella|literature|integrative|rapid)\s+reviews?"
    r"|meta[-\s]?analys(?:is|es)"
    r"|(?:clinical\s+practice|consensus|evidence[- ]based)\s+(?:guidelines?|recommendations))\b"
    r"|^(?:(?:international|clinical|practice)\s+)?(?:guidelines?|recommendations)\s+(?:for|on|from)\b",
    re.IGNORECASE,
)


def is_nonprimary_work(work: dict, title: str) -> bool:
    """Recognize explicit synthesis/guideline titles without treating peer review as a review."""
    kind = work.get("type")
    return (kind is not None and kind not in {"article", "preprint", "dissertation"}) or bool(REVIEW_TITLE_RE.search(title))


def _decoded(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _object(value) -> dict:
    parsed = _decoded(value)
    return parsed if isinstance(parsed, dict) else {}


def _array(value) -> list:
    parsed = _decoded(value)
    if isinstance(parsed, list):
        return parsed
    return [parsed] if isinstance(parsed, str) and parsed else []


def reconstruct_abstract(inverted: Any) -> str:
    """Rebuild sparse indexes without allocating according to an untrusted position.

    Parquet exports may encode the index as a JSON string or DuckDB MAP. Invalid
    positions are ignored; collisions choose the lexicographically first token.
    """
    if isinstance(inverted, str):
        try:
            inverted = json.loads(inverted)
        except json.JSONDecodeError:
            return ""
    if isinstance(inverted, list):
        try:
            inverted = dict(inverted)
        except (ValueError, TypeError):
            return ""
    if not isinstance(inverted, dict):
        return ""
    # Older Arrow MAP conversion produces parallel key/value lists.
    if set(inverted) == {"key", "value"}:
        inverted = dict(zip(inverted["key"], inverted["value"], strict=False))
    positions: dict[int, str] = {}
    for word, offsets in sorted(inverted.items(), key=lambda item: str(item[0])):
        if not isinstance(word, str) or not isinstance(offsets, (list, tuple)):
            continue
        for position in offsets:
            if isinstance(position, int) and not isinstance(position, bool) and position >= 0:
                positions.setdefault(position, word)
    return " ".join(positions[position] for position in sorted(positions))


def normalize_work(work: dict, *, require_abstract: bool = False) -> dict | None:
    abstract = reconstruct_abstract(work.get("abstract_inverted_index"))
    if require_abstract and not abstract:
        return None
    identifier = str(work.get("id", "")).rstrip("/").rsplit("/", 1)[-1]
    if not re.fullmatch(r"W\d+", identifier):
        raise ValueError("OpenAlex work requires a valid W identifier")
    study = empty_study(identifier, "openalex")
    ids = _object(work.get("ids"))
    paper_pmid = pmid(ids.get("pmid") or work.get("pmid"))
    title = work.get("title") or work.get("display_name") or "Untitled study"
    year = integer(work.get("publication_year"))
    location = _object(work.get("primary_location"))
    venue = _object(location.get("source"))
    study.update({
        "title": title, "abstract": abstract,
        "abstract_available": bool(abstract), "work_type": work.get("type") or "unknown",
        "authors": [author["display_name"]
                    for item in _array(work.get("authorships"))
                    if (author := _object(_object(item).get("author"))).get("display_name")],
        "venue": venue.get("display_name", ""),
        "url": ids.get("doi") or work.get("doi") or f"https://openalex.org/{identifier}",
        "year": year, "publication_date": f"{year:04d}-01-01" if year else None,
        "pmids": [paper_pmid] if paper_pmid else [],
        "nct_ids": sorted({x.upper() for x in NCT_PATTERN.findall(abstract)}),
        "referenced_works": [str(x).rstrip("/").rsplit("/", 1)[-1]
                             for x in _array(work.get("referenced_works"))],
        "is_retracted": bool(work.get("is_retracted", False)),
        "is_review": is_nonprimary_work(work, title),
        "normalizer_version": NORMALIZER_VERSION,
        "snapshot_provenance": work.get("snapshot_provenance", {}),
        "cited_by_count": integer(work.get("cited_by_count")) or 0,
        "topics": [topic.get("display_name", "") if isinstance(topic, dict) else str(topic)
                   for topic in _array(work.get("topics"))],
    })
    return study
