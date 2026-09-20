"""Open-access full text for a single paper, fetched and sectioned at read time.

An abstract states what a paper claims; only the body states what it actually measured,
in which system, at which dose, and what the numbers were. Novelty assessment reads the
body, so this module resolves a work to Europe PMC and returns its sections.
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from xml.etree import ElementTree

import httpx

logger = logging.getLogger(__name__)

EUROPE_PMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
RESULT_SECTIONS = ("results", "discussion", "conclusion")
# Front and back matter carries no evidence and would crowd the read budget.
SKIP_SECTIONS = (
    "reference", "acknowledg", "competing", "author contribution", "funding", "footnote",
    "contributor", "data availability", "supplementary", "associated data", "ethics",
    "abbreviation", "conflict",
)
# Headings vary between journals; match the informative ones by their stem.
SECTION_KINDS = {
    "abstract": ("abstract", "summary"),
    "methods": ("method", "material", "experimental", "procedure"),
    "results": ("result", "finding"),
    "discussion": ("discussion", "conclusion", "interpretation"),
}


@dataclass
class FullText:
    """One paper's retrieved body, or the abstract when no open full text exists."""

    paper_id: str
    availability: str  # full_text | abstract_only | unavailable
    source: str = ""
    sections: dict[str, str] = field(default_factory=dict)
    # As the publisher record states them, which the index can contradict.
    title: str = ""
    year: int | None = None
    venue: str = ""

    @property
    def chars(self) -> int:
        return sum(len(text) for text in self.sections.values())

    def evidence_text(self, limit: int = 24000) -> str:
        """The part a reader checks for what was found, results first, then the rest."""
        order = [*RESULT_SECTIONS, "abstract", "methods"]
        ordered = sorted(
            self.sections.items(),
            key=lambda item: next(
                (i for i, name in enumerate(order) if name in item[0]), len(order)
            ),
        )
        out: list[str] = []
        remaining = limit
        for name, text in ordered:
            if remaining <= 0:
                break
            body = text[:remaining]
            remaining -= len(body) + len(name)
            out.append(f"## {name}\n{body}")
        return "\n\n".join(out)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _kind(heading: str) -> str:
    lowered = heading.lower()
    if any(needle in lowered for needle in SKIP_SECTIONS):
        return ""
    for kind, needles in SECTION_KINDS.items():
        if any(needle in lowered for needle in needles):
            return kind
    return heading.lower()[:60] or "body"


def parse_jats(xml: str) -> dict[str, str]:
    """JATS body into `{section kind: text}`; unrecognised headings keep their own name."""
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        logger.warning("Full text is not parseable XML: %s", exc)
        return {}
    sections: dict[str, str] = {}

    def add(name: str, text: str) -> None:
        text = _clean(text)
        if not name:
            return
        if len(text) < 40:
            return
        sections[name] = f"{sections[name]}\n{text}" if name in sections else text

    for abstract in root.iter("abstract"):
        add("abstract", " ".join(abstract.itertext()))
    body = root.find(".//body")
    if body is not None:
        for section in body.findall("sec"):
            title = section.find("title")
            heading = _clean("".join(title.itertext())) if title is not None else "body"
            add(_kind(heading), " ".join(section.itertext()))

        if not sections.keys() - {"abstract"}:
            add("body", " ".join(body.itertext()))
    return sections


class FullTextClient:
    """Europe PMC lookup: DOI or PMID to an open-access body, cached per process."""

    def __init__(self, client: httpx.AsyncClient | None = None, timeout: float = 30.0):
        self._client = client
        self._timeout = timeout
        self._cache: dict[str, FullText] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def locate(self, doi: str = "", pmid: str = "") -> dict:
        """The Europe PMC record for a work, which carries its PMCID when one exists."""
        doi = (doi or "").replace("https://doi.org/", "").strip()
        query = f'DOI:"{doi}"' if doi else f"EXT_ID:{pmid} AND SRC:MED" if pmid else ""
        if not query:
            return {}
        client = await self._http()
        response = await client.get(
            f"{EUROPE_PMC}/search",
            params={"query": query, "format": "json", "resultType": "core", "pageSize": 1},
        )
        response.raise_for_status()
        results = response.json().get("resultList", {}).get("result", [])
        return results[0] if results else {}

    async def fetch(self, paper_id: str, doi: str = "", pmid: str = "", abstract: str = "") -> FullText:
        lock = self._locks.setdefault(paper_id, asyncio.Lock())
        async with lock:
            if paper_id in self._cache:
                return self._cache[paper_id]
            result = await self._fetch(paper_id, doi, pmid, abstract)
            self._cache[paper_id] = result
            return result

    async def _fetch(self, paper_id: str, doi: str, pmid: str, abstract: str) -> FullText:
        fallback = FullText(
            paper_id,
            "abstract_only" if abstract else "unavailable",
            sections={"abstract": abstract} if abstract else {},
        )
        try:
            record = await self.locate(doi, pmid)
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Full-text lookup failed for %s: %s", paper_id, type(exc).__name__)
            return fallback
        # Europe PMC titles keep inline markup such as <sub> from the publisher record.
        fallback.title = _clean(re.sub(r"<[^>]+>", "", str(record.get("title") or "")))
        fallback.year = int(year) if (year := str(record.get("pubYear") or "")).isdigit() else None
        journal = record.get("journalInfo")
        title = journal.get("journal", {}).get("title") if isinstance(journal, dict) else ""
        fallback.venue = str(title or record.get("journalTitle") or "")
        pmcid = record.get("pmcid")
        if not pmcid or record.get("isOpenAccess") != "Y":
            return fallback
        client = await self._http()
        try:
            response = await client.get(f"{EUROPE_PMC}/{pmcid}/fullTextXML")
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.info("No open body for %s (%s)", pmcid, type(exc).__name__)
            return fallback
        sections = parse_jats(response.text)
        if not sections:
            return fallback
        if abstract and "abstract" not in sections:
            sections["abstract"] = abstract
        return FullText(
            paper_id,
            "full_text",
            source=f"europepmc:{pmcid}",
            sections=sections,
            title=fallback.title,
            year=fallback.year,
            venue=fallback.venue,
        )
