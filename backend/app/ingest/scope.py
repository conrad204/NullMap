"""Versioned, inspectable corpus eligibility; no year/type/abstract restriction."""

import json
import re

from app.ingest.openalex import reconstruct_abstract

SCOPE_VERSION = "hypertension-kidney-v1"
PROFILES = ("hypertension-kidney", "hypertension-kidney-pubmed", "all")
# The -pubmed profile is the same topical scope restricted to PubMed-indexed works.
# Update-date partitions differ enormously in quality: some hold almost no PMIDs or
# DOIs and many records whose title and abstract belong to different works. Registry
# linking and Europe PMC full text both need the PubMed identity, so a corpus meant
# for trial evidence can require it. It is opt-in and recorded as its own scope.
PUBMED_SUFFIX = "-pubmed"
TOPIC_IDS = {
    "T10144",
    "T11839",
    "T10728",
    "T12910",
    "T11001",
    "T10373",
    "T10449",
    "T10606",
    "T11174",
}
WORD_PATTERN = (
    r"\b(?:hypertens\w*|kidney\w*|renal|renovascular|nephro\w*|glomerul\w*|"
    r"dialys\w*|hemodial\w*|haemodial\w*|uremi\w*|uraemi\w*|"
    r"albuminuri\w*|proteinuri\w*|podocyt\w*|ureter\w*|urolith\w*|"
    r"CKD|ESRD|ESKD|AKI|ADPKD|ARPKD)\b"
)
WORDS = re.compile(WORD_PATTERN, re.IGNORECASE)
PHRASES = re.compile(r"\b(?:high|elevated)\s+blood\s+pressure\b", re.IGNORECASE)
CTGOV_QUERY = (
    'AREA[ConditionSearch](hypertension OR "high blood pressure" OR kidney OR renal OR '
    "nephrology OR nephropathy OR glomerulonephritis OR dialysis OR hemodialysis OR "
    'haemodialysis OR "chronic kidney disease" OR "acute kidney injury" OR '
    '"end stage renal disease" OR proteinuria OR albuminuria OR nephrolithiasis)'
)


def is_pubmed_indexed(work: dict) -> bool:
    ids = work.get("ids")
    if isinstance(ids, str):
        try:
            ids = json.loads(ids)
        except json.JSONDecodeError:
            ids = {}
    indexed = work.get("indexed_in")
    return bool(
        (isinstance(ids, dict) and ids.get("pmid"))
        or work.get("pmid")
        or (isinstance(indexed, (list, tuple)) and "pubmed" in indexed)
    )


def matches_work(work: dict, profile: str = "hypertension-kidney", topic: str = "") -> bool:
    if profile not in PROFILES:
        raise ValueError(f"Unknown scope: {profile}")
    if profile.endswith(PUBMED_SUFFIX):
        return is_pubmed_indexed(work) and matches_work(
            work, profile.removesuffix(PUBMED_SUFFIX), topic
        )
    topics = work.get("topics") or []
    topic_text = json.dumps(topics, ensure_ascii=False).lower()
    if topic and topic.lower() not in topic_text:
        return False
    if profile == "all":
        return True
    if isinstance(topics, list):
        for item in topics:
            if not isinstance(item, dict):
                continue
            if str(item.get("id", "")).rsplit("/", 1)[-1] in TOPIC_IDS:
                return True
            subfield = item.get("subfield") or {}
            if (
                isinstance(subfield, dict)
                and str(subfield.get("id", "")).rsplit("/", 1)[-1] == "2727"
            ):
                return True
    text = " ".join(
        [
            str(work.get("title") or work.get("display_name") or ""),
            reconstruct_abstract(work.get("abstract_inverted_index")),
            topic_text,
        ]
    )
    return bool(WORDS.search(text) or PHRASES.search(text))


def coarse_predicate(columns: set[str], profile: str) -> str:
    """Cheap SQL prefilter; exact phrase matching follows abstract reconstruction."""
    if profile == "all":
        return "true"
    if profile.endswith(PUBMED_SUFFIX):
        # Text casts keep this valid whether ids is a MAP, a STRUCT or a JSON string.
        identity = [
            f"lower(CAST(\"{name}\" AS VARCHAR)) LIKE '%{needle}%'"
            for name, needle in (("ids", "pmid"), ("indexed_in", "pubmed"))
            if name in columns
        ]
        if "pmid" in columns:
            identity.append('"pmid" IS NOT NULL')
        topical = coarse_predicate(columns, profile.removesuffix(PUBMED_SUFFIX))
        return f"({' OR '.join(identity) or 'false'}) AND {topical}"
    fields = [
        f"coalesce(CAST(\"{name}\" AS VARCHAR), '')"
        for name in ("title", "display_name", "abstract_inverted_index", "topics")
        if name in columns
    ]
    text = "lower(" + " || ' ' || ".join(fields) + ")"
    # Inverted indexes do not preserve phrase order. Retain pressure candidates
    # here, then match PHRASES against the reconstructed abstract.
    patterns = [
        "hypertens",
        "kidney",
        "renal",
        "nephro",
        "glomerul",
        "dialys",
        "hemodial",
        "haemodial",
        "uremi",
        "uraemi",
        "albuminuri",
        "proteinuri",
        "podocyt",
        "ureter",
        "urolith",
        "ckd",
        "esrd",
        "eskd",
        "aki",
        "adpkd",
        "arpkd",
        "pressure",
        "2727",
        *sorted(identifier.lower() for identifier in TOPIC_IDS),
    ]
    return f"regexp_matches({text}, '{'|'.join(patterns)}')"
