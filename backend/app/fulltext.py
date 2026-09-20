"""Query-time full text from Europe PMC for open-access papers with a PMCID.

The OpenAlex snapshot carries abstracts only. When a paper is in PubMed Central,
Europe PMC serves its JATS XML without a key. This module flattens that XML into
verbatim lines (abstract sentences, primary-outcome sentences from the methods,
results sentences, and table rows) so the existing sentence-index extraction and
its exact-quote validation work unchanged. Papers without a PMCID stay abstract-only.
"""

from __future__ import annotations

import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import httpx

from app.config import Settings, settings

logger = logging.getLogger(__name__)

PMCID_RE = re.compile(r"PMC(\d{1,9})", re.IGNORECASE)
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\[])")
PRIMARY_RE = re.compile(
    r"\b(?:primary|principal|main)\s+(?:efficacy\s+|safety\s+|composite\s+)?(?:outcomes?|end[- ]?points?|"
    r"objectives?)\b|\bsample size\b|\bpower(?:ed)? to\b|\bnon-?inferiority margin\b",
    re.IGNORECASE,
)
METHODS_RE = re.compile(
    r"\b(?:methods?|materials|design|participants|patients|procedures?|statistic|analysis|"
    r"outcomes? measures?|end ?points?|trial|study population|intervention)\b",
    re.IGNORECASE,
)
RESULTS_RE = re.compile(r"\b(?:results?|findings|outcomes?|efficacy|effectiveness)\b", re.IGNORECASE)
EXCLUDED_RE = re.compile(
    r"\b(?:discussion|conclusions?|limitations|acknowledg|funding|conflicts?|competing|"
    r"references?|supplementa|abbreviations|author contributions|ethic|consent|availability)\b",
    re.IGNORECASE,
)
TABLE_CELL_TAGS = {"td", "th"}


def normalize_pmcid(value) -> str | None:
    """Accept 'PMC123', 'pmc123', a PMC URL, or bare digits; return 'PMC123' or None."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    match = PMCID_RE.search(text)
    if match:
        return f"PMC{int(match.group(1))}"
    if text.isdigit():
        return f"PMC{int(text)}"
    return None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return re.sub(r"\s+", " ", "".join(element.itertext())).strip()


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in SENTENCE_RE.split(text) if len(part.strip()) > 2]


def _paragraph_texts(section: ET.Element) -> list[str]:
    """Paragraph text of this section only, not of nested subsections or tables."""
    out = []
    for child in section:
        name = _local(child.tag)
        if name == "p":
            text = _text(child)
            if text:
                out.append(text)
        elif name in {"list", "disp-quote", "boxed-text", "statement"}:
            for para in child.iter():
                if _local(para.tag) == "p" and (text := _text(para)):
                    out.append(text)
    return out


def _table_lines(wrap: ET.Element) -> list[str]:
    label = _text(next((c for c in wrap if _local(c.tag) == "label"), None))
    caption = _text(next((c for c in wrap if _local(c.tag) == "caption"), None))
    heading = " ".join(part for part in (label, caption) if part).strip()
    prefix = f"[{heading}] " if heading else "[Table] "
    lines: list[str] = []
    for table in wrap.iter():
        if _local(table.tag) != "table":
            continue
        headers: list[str] = []
        for row in table.iter():
            if _local(row.tag) != "tr":
                continue
            cells = [_text(cell) for cell in row if _local(cell.tag) in TABLE_CELL_TAGS]
            cells = [cell for cell in cells if cell]
            if not cells:
                continue
            is_header = all(_local(cell.tag) == "th" for cell in row if _local(cell.tag) in TABLE_CELL_TAGS)
            if is_header and not headers:
                headers = cells
                lines.append(prefix + "columns: " + " | ".join(cells))
                continue
            lines.append(prefix + " | ".join(cells))
    return lines


BASELINE_TABLE_RE = re.compile(
    r"\b(?:baseline|demographic|characteristics|enrol+ment|disposition|adverse|safety|"
    r"tolerability)\b",
    re.IGNORECASE,
)
OUTCOME_TABLE_RE = re.compile(r"\b(?:primary|outcome|end ?point|efficacy|effect)\b", re.IGNORECASE)


def _table_priority(wrap: ET.Element) -> int:
    """0 for tables that name outcomes, 2 for baseline/safety tables, 1 otherwise."""
    heading = " ".join(
        _text(c) for c in wrap if _local(c.tag) in {"label", "caption"}
    )
    if OUTCOME_TABLE_RE.search(heading):
        return 0
    if BASELINE_TABLE_RE.search(heading):
        return 2
    return 1


def _section_kind(section: ET.Element, inherited: str | None) -> str | None:
    sec_type = (section.get("sec-type") or "").lower()
    title = _text(next((c for c in section if _local(c.tag) == "title"), None))
    if EXCLUDED_RE.search(sec_type) or EXCLUDED_RE.search(title):
        return "excluded"
    if "result" in sec_type:
        return "results"
    if "method" in sec_type or "material" in sec_type:
        return "methods"
    # Subsections inherit: "Outcomes" under Methods defines endpoints, under Results reports them.
    if inherited in ("methods", "results"):
        return inherited
    if RESULTS_RE.search(title):
        return "results"
    if METHODS_RE.search(title):
        return "methods"
    return inherited


@dataclass
class FlattenedText:
    lines: list[str] = field(default_factory=list)
    abstract_lines: int = 0
    methods_lines: int = 0
    results_lines: int = 0
    table_lines: int = 0
    truncated: bool = False

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def summary(self) -> dict:
        return {
            "lines": len(self.lines),
            "abstract": self.abstract_lines,
            "methods_primary": self.methods_lines,
            "results": self.results_lines,
            "tables": self.table_lines,
            "truncated": self.truncated,
        }


def flatten_jats(xml_text: str, abstract: str = "", *, max_lines: int = 160) -> FlattenedText:
    """Turn JATS XML into verbatim candidate evidence lines, highest value first.

    Order and budget: abstract sentences, then methods sentences that define the
    primary outcome or power, then results prose, then table rows with baseline and
    demographics tables last so outcome tables survive the line budget. Discussion,
    conclusions and back matter are never included; the numbers we want live in
    results tables and results prose, and a discussion sentence can restate a
    secondary finding as though it were the primary one.
    """
    result = FlattenedText()
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return result
    body = next((el for el in root.iter() if _local(el.tag) == "body"), None)
    abstract_sentences = _sentences(abstract) if abstract else []
    if not abstract_sentences:
        for el in root.iter():
            if _local(el.tag) == "abstract":
                # Structured abstracts nest <p> inside <sec>; take every paragraph.
                paragraphs = [_text(p) for p in el.iter() if _local(p.tag) == "p"]
                abstract_sentences = [s for p in paragraphs if p for s in _sentences(p)]
                break
    methods: list[str] = []
    results: list[str] = []
    table_groups: list[tuple[int, list[str]]] = []
    if body is not None:
        def walk(section: ET.Element, inherited: str | None):
            kind = _section_kind(section, inherited)
            if kind == "excluded":
                return
            paragraphs = _paragraph_texts(section)
            if kind == "results":
                results.extend(s for p in paragraphs for s in _sentences(p))
            elif kind == "methods":
                methods.extend(s for p in paragraphs for s in _sentences(p) if PRIMARY_RE.search(s))
            for child in section:
                name = _local(child.tag)
                if name == "sec":
                    walk(child, kind)
                elif name == "table-wrap":
                    table_groups.append((_table_priority(child), _table_lines(child)))
        for child in body:
            name = _local(child.tag)
            if name == "sec":
                walk(child, None)
            elif name == "table-wrap":
                table_groups.append((_table_priority(child), _table_lines(child)))
            elif name == "p":
                # Body text without sections: treat as results prose.
                results.extend(_sentences(_text(child)))
    seen: set[str] = set()

    def take(group: list[str], counter: str) -> None:
        for line in group:
            if len(result.lines) >= max_lines:
                result.truncated = True
                return
            if line in seen:
                continue
            seen.add(line)
            result.lines.append(line)
            setattr(result, counter, getattr(result, counter) + 1)

    tables = [line for _, group in sorted(table_groups, key=lambda item: item[0]) for line in group]
    take(abstract_sentences, "abstract_lines")
    take(methods, "methods_lines")
    take(results, "results_lines")
    take(tables, "table_lines")
    return result


FETCH_CONCURRENCY = 8


class FullTextClient:
    """Fetch and flatten one paper's Europe PMC full text; never raises for a missing paper."""

    def __init__(self, config: Settings = settings, transport: httpx.AsyncBaseTransport | None = None):
        self.config = config
        self.transport = transport
        # One search can read a hundred papers; Europe PMC is a shared public service.
        self.slots = asyncio.Semaphore(FETCH_CONCURRENCY)

    def pmcid(self, study: dict) -> str | None:
        return normalize_pmcid(study.get("pmcid"))

    async def fetch_xml(self, pmcid: str) -> str | None:
        url = f"{self.config.europepmc_url.rstrip('/')}/{pmcid}/fullTextXML"
        async with (
            self.slots,
            httpx.AsyncClient(
                timeout=self.config.fulltext_timeout,
                transport=self.transport,
                follow_redirects=True,
            ) as client,
        ):
            response = await client.get(url, headers={"Accept": "application/xml"})
        if response.status_code == 404:
            return None
        response.raise_for_status()
        text = response.text
        # Europe PMC answers 200 with an empty body for records without full text.
        return text if text.strip() else None

    async def lines(self, study: dict) -> tuple[list[str] | None, str | None]:
        """Return (lines, status). status: None (no PMCID), 'used', 'unavailable', or 'error'."""
        pmcid = self.pmcid(study)
        if not pmcid or not self.config.fulltext_enabled:
            return None, None
        try:
            xml_text = await self.fetch_xml(pmcid)
        except Exception as exc:
            logger.warning("Full text fetch failed for %s: %s", pmcid, type(exc).__name__)
            return None, "error"
        if xml_text is None:
            return None, "unavailable"
        flattened = flatten_jats(
            xml_text, study.get("abstract", ""), max_lines=self.config.fulltext_max_lines
        )
        # Full text only counts when it adds evidence beyond the abstract.
        if flattened.results_lines + flattened.table_lines + flattened.methods_lines == 0:
            return None, "unavailable"
        logger.info("Full text %s: %s", pmcid, flattened.summary())
        return flattened.lines, "used"
