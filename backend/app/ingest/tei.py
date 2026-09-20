"""Deterministic null-hypothesis evidence extraction from GROBID TEI XML.

Full-text papers are parsed into sections, paragraphs, tables and appendices.
A dictionary of statistical terms (confidence intervals, p-values, correlations,
effect sizes, sample sizes) locates candidate sentences; the paragraph around each
candidate is scanned for null-result confirmations ("no significant difference",
non-significant p-values, confidence intervals that straddle the null value); and
the associated numbers are extracted from the body text, tables and appendix.

The output reuses the study fields consumed by ``app.statistics`` and
``app.classifier`` (estimate, ci_low/ci_high, p_value, effect_type, n,
result_label, null_score, evidence_span) so a TEI record can flow through the same
classification and pooling code as OpenAlex and ClinicalTrials.gov records. This
is an auditable heuristic: every finding keeps the exact sentence it came from and
is never presented as a calibrated probability.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

from app.classifier import NULL_RE, POSITIVE_RE
from app.ingest.common import empty_study, integer, number

TEI_EXTRACTOR_VERSION = "tei-null-v1"

# --- Statistical term dictionary -------------------------------------------------
# Presence of these phrases marks a sentence as statistically relevant even when a
# machine-readable number cannot be parsed; categories are reported on each finding.
STAT_TERMS: dict[str, tuple[str, ...]] = {
    "confidence_interval": ("confidence interval", "credible interval", "ci", "95% ci", "95 % ci"),
    "p_value": ("p-value", "p value", "p-values", "significance level", "alpha level"),
    "correlation": (
        "correlation",
        "correlation coefficient",
        "pearson",
        "spearman",
        "kendall",
    ),
    "effect_size": (
        "effect size",
        "odds ratio",
        "risk ratio",
        "relative risk",
        "hazard ratio",
        "mean difference",
        "standardized mean difference",
        "standardised mean difference",
        "regression coefficient",
        "cohen's d",
        "hedges' g",
        "beta coefficient",
    ),
    "sample_size": ("sample size", "participants", "subjects", "enrolled", "respondents"),
    "significance": (
        "null hypothesis",
        "statistically significant",
        "statistical significance",
        "non-significant",
        "nonsignificant",
        "not significant",
    ),
}

_TERM_RES: dict[str, re.Pattern[str]] = {
    category: re.compile(
        r"|".join(rf"\b{re.escape(term)}\b" for term in terms), re.IGNORECASE
    )
    for category, terms in STAT_TERMS.items()
}

# --- Number-shaped fragments -----------------------------------------------------
_NUM = r"[-−+]?\d*\.?\d+(?:[eE][-−+]?\d+)?"
_SCI10 = r"(?:\s*[×x]\s*10\s*\^?\s*[-−]?\d+)"

P_VALUE_RE = re.compile(
    rf"\bp(?:[-\s]?values?)?\s*(?P<op><=|>=|<|>|=|≤|≥)\s*(?P<val>{_NUM}{_SCI10}?)",
    re.IGNORECASE,
)
NONSIGNIFICANT_RE = re.compile(r"\b(?:n\.?\s?s\.?|non[-\s]?significant|not significant)\b", re.IGNORECASE)

CI_RE = re.compile(
    rf"(?:(?P<level>\d{{2,3}})\s*%\s*)?"
    rf"(?:c\.?\s?i\.?|confidence interval|credible interval)\s*[:=]?\s*(?:of\s*|was\s*|:\s*)?"
    rf"[\[\(]?\s*(?P<low>{_NUM})\s*(?:,|;|\bto\b|–|—|‐|-|−|\band\b)\s*(?P<high>{_NUM})\s*[\]\)]?",
    re.IGNORECASE,
)

# Bare bracketed interval that follows an estimate, e.g. "1.20 (0.95-1.60)".
BRACKET_INTERVAL_RE = re.compile(
    rf"[\[\(]\s*(?P<low>{_NUM})\s*(?:,|;|\bto\b|–|—|‐|-|−)\s*(?P<high>{_NUM})\s*[\]\)]"
)

# Effect-size tokens mapped to the normalized effect_type understood by statistics.
_EFFECT_TOKENS: tuple[tuple[str, str | None], ...] = (
    ("adjusted odds ratio", "OR"),
    ("adjusted hazard ratio", "HR"),
    ("standardized mean difference", "SMD"),
    ("standardised mean difference", "SMD"),
    ("weighted mean difference", "MD"),
    ("mean difference", "MD"),
    ("odds ratio", "OR"),
    ("risk ratio", "RR"),
    ("relative risk", "RR"),
    ("hazard ratio", "HR"),
    ("cohen's d", "SMD"),
    ("hedges' g", "SMD"),
    ("aOR", "OR"),
    ("aHR", "HR"),
    ("SMD", "SMD"),
    ("WMD", "MD"),
    ("OR", "OR"),
    ("RR", "RR"),
    ("HR", "HR"),
    ("MD", "MD"),
    ("d", "SMD"),
    ("g", "SMD"),
)
_EFFECT_TYPE_BY_TOKEN = {token.lower(): kind for token, kind in _EFFECT_TOKENS}
_EFFECT_ALTERNATION = "|".join(
    re.escape(token) for token, _ in sorted(_EFFECT_TOKENS, key=lambda t: len(t[0]), reverse=True)
)
EFFECT_RE = re.compile(
    rf"(?<![A-Za-z])(?P<name>{_EFFECT_ALTERNATION})\s*(?:=|:|of|was|,)?\s*(?P<val>{_NUM})(?![%\d])",
    re.IGNORECASE,
)

CORRELATION_RE = re.compile(
    r"(?<![A-Za-z])(?P<name>r|ρ|rho|r_s|rs)\s*(?:\(\s*\d+\s*\))?\s*=\s*(?P<val>[-−+]?\.?\d+\.?\d*)",
    re.IGNORECASE,
)

SAMPLE_SIZE_RE = re.compile(r"(?<![A-Za-z])[nN]\s*=\s*(?P<val>\d[\d,]*)")

_RATIO_TYPES = {"OR", "RR", "HR", "logOR", "logRR", "logHR"}
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\[])")


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text_of(element: ElementTree.Element) -> str:
    return " ".join("".join(element.itertext()).split())


def _parse_number(raw: str | None) -> float | None:
    if raw is None:
        return None
    cleaned = raw.strip().replace("−", "-").replace("–", "-").replace(" ", "")
    scientific = re.fullmatch(r"([-+]?\d*\.?\d+)[×x]10\^?([-+]?\d+)", cleaned)
    if scientific:
        try:
            return float(scientific.group(1)) * (10 ** int(scientific.group(2)))
        except (ValueError, OverflowError):
            return None
    return number(cleaned)


# --- Parsed document model -------------------------------------------------------
@dataclass
class Paragraph:
    text: str
    section: str
    part: str  # abstract | body | appendix | table_caption


@dataclass
class Table:
    caption: str
    rows: list[list[str]]
    section: str
    part: str  # body | appendix


@dataclass
class TeiDocument:
    title: str = ""
    doi: str = ""
    abstract: str = ""
    paragraphs: list[Paragraph] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)


def _is_appendix(head: str, div_type: str) -> bool:
    haystack = f"{head} {div_type}".lower()
    return any(word in haystack for word in ("appendix", "annex", "supplement", "supporting"))


def _collect_tables(container: ElementTree.Element, section: str, part: str) -> list[Table]:
    tables: list[Table] = []
    for figure in container.iter():
        if _localname(figure.tag) != "figure" or figure.get("type") != "table":
            continue
        caption = ""
        rows: list[list[str]] = []
        for child in figure.iter():
            name = _localname(child.tag)
            if name in {"figDesc", "head"} and not caption:
                caption = _text_of(child)
            if name == "row":
                rows.append([_text_of(cell) for cell in child if _localname(cell.tag) == "cell"])
        tables.append(Table(caption=caption, rows=rows, section=section, part=part))
    return tables


def parse_tei(xml: str) -> TeiDocument:
    """Parse GROBID-style TEI XML into sections, paragraphs, tables and appendices."""
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise ValueError(f"Invalid TEI XML: {exc}") from None

    document = TeiDocument()
    for element in root.iter():
        name = _localname(element.tag)
        if name == "title" and not document.title and element.get("type") != "sub":
            document.title = _text_of(element)
        elif name == "idno" and not document.doi and (element.get("type") or "").upper() == "DOI":
            document.doi = _text_of(element)

    # Abstract lives in the header's profileDesc.
    for element in root.iter():
        if _localname(element.tag) == "abstract":
            paragraphs = [
                _text_of(paragraph)
                for paragraph in element.iter()
                if _localname(paragraph.tag) == "p"
            ]
            document.abstract = " ".join(p for p in paragraphs if p) or _text_of(element)
            for paragraph in paragraphs:
                if paragraph:
                    document.paragraphs.append(Paragraph(paragraph, "Abstract", "abstract"))
            break

    body_like = [
        element
        for element in root.iter()
        if _localname(element.tag) in {"body", "back"}
    ]
    for container in body_like:
        for div in container.iter():
            if _localname(div.tag) != "div":
                continue
            head = ""
            for child in div:
                if _localname(child.tag) == "head":
                    head = _text_of(child)
                    break
            div_type = div.get("type", "")
            # Skip the bibliography; references are not study results.
            if "reference" in f"{head} {div_type}".lower():
                continue
            part = "appendix" if _is_appendix(head, div_type) else "body"
            for child in div:
                if _localname(child.tag) == "p":
                    text = _text_of(child)
                    if text:
                        document.paragraphs.append(Paragraph(text, head, part))
        document.tables.extend(_collect_tables(container, "", "body"))
    return document


# --- Numeric extraction ----------------------------------------------------------
@dataclass
class Match:
    start: int
    end: int
    value: float | None = None
    low: float | None = None
    high: float | None = None
    level: float | None = None
    operator: str = ""
    effect_type: str | None = None
    effect_name: str | None = None
    used: bool = False


def _p_values(text: str) -> list[Match]:
    matches: list[Match] = []
    for hit in P_VALUE_RE.finditer(text):
        value = _parse_number(hit.group("val"))
        if value is None:
            continue
        operator = hit.group("op").replace("<=", "≤").replace(">=", "≥")
        matches.append(Match(hit.start(), hit.end(), value=value, operator=operator))
    return matches


def _confidence_intervals(text: str) -> list[Match]:
    matches: list[Match] = []
    for hit in CI_RE.finditer(text):
        low, high = _parse_number(hit.group("low")), _parse_number(hit.group("high"))
        if low is None or high is None:
            continue
        if low > high:
            low, high = high, low
        level = _parse_number(hit.group("level"))
        matches.append(Match(hit.start(), hit.end(), low=low, high=high, level=level))
    return matches


def _effects(text: str) -> list[Match]:
    matches: list[Match] = []
    for hit in EFFECT_RE.finditer(text):
        value = _parse_number(hit.group("val"))
        if value is None:
            continue
        name = hit.group("name")
        matches.append(
            Match(
                hit.start(),
                hit.end(),
                value=value,
                effect_type=_EFFECT_TYPE_BY_TOKEN.get(name.lower()),
                effect_name=name,
            )
        )
    return matches


def _correlations(text: str) -> list[Match]:
    matches: list[Match] = []
    for hit in CORRELATION_RE.finditer(text):
        value = _parse_number(hit.group("val"))
        if value is None or not -1.0 <= value <= 1.0:
            continue
        matches.append(Match(hit.start(), hit.end(), value=value, effect_name=hit.group("name")))
    return matches


def _sample_sizes(text: str) -> list[Match]:
    return [
        Match(hit.start(), hit.end(), value=_parse_number(hit.group("val")))
        for hit in SAMPLE_SIZE_RE.finditer(text)
    ]


def _nearest(anchor: int, candidates: list[Match]) -> Match | None:
    best: Match | None = None
    best_distance = 10**9
    for candidate in candidates:
        if candidate.used:
            continue
        # An interval or p-value usually trails its estimate; bias slightly toward
        # candidates that appear after the anchor.
        distance = candidate.start - anchor
        weighted = distance if distance >= 0 else -distance * 2
        if weighted < best_distance:
            best, best_distance = candidate, weighted
    return best


# --- Finding assembly ------------------------------------------------------------
@dataclass
class Finding:
    sentence: str
    section: str
    part: str
    terms: list[str]
    result_label: str = "no_result_stated"
    null_score: float = 0.0
    is_null: bool = False
    is_significant: bool = False
    estimate: float | None = None
    effect_type: str | None = None
    effect_name: str | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    ci_level: float | None = None
    p_value: float | None = None
    p_value_operator: str = ""
    p_value_is_exact: bool | None = None
    correlation: float | None = None
    n: int | None = None

    def to_dict(self) -> dict:
        return {
            "sentence": self.sentence,
            "section": self.section,
            "part": self.part,
            "terms": self.terms,
            "result_label": self.result_label,
            "null_score": round(self.null_score, 3),
            "is_null": self.is_null,
            "is_significant": self.is_significant,
            "estimate": self.estimate,
            "effect_type": self.effect_type,
            "effect_name": self.effect_name,
            "ci_low": self.ci_low,
            "ci_high": self.ci_high,
            "ci_level": self.ci_level,
            "p_value": self.p_value,
            "p_value_operator": self.p_value_operator,
            "p_value_is_exact": self.p_value_is_exact,
            "correlation": self.correlation,
            "n": self.n,
        }


def _null_value_for(finding: Finding) -> float | None:
    """The value a confidence interval must straddle to confirm the null."""
    if finding.effect_type in _RATIO_TYPES:
        return 1.0
    if finding.effect_type in {"SMD", "MD"} or finding.correlation is not None:
        return 0.0
    if finding.effect_name and finding.effect_name.lower() in {"beta", "b", "d", "g"}:
        return 0.0
    return None


def _classify_finding(finding: Finding, null_context: bool, significant_context: bool) -> None:
    numeric_null = numeric_significant = False
    if finding.p_value is not None:
        operator = finding.p_value_operator
        if operator in {"<", "≤"}:
            numeric_significant = finding.p_value <= 0.05
        elif operator in {">", "≥"}:
            numeric_null = finding.p_value >= 0.05
        else:  # "="
            numeric_significant = finding.p_value < 0.05
            numeric_null = finding.p_value > 0.05
    null_value = _null_value_for(finding)
    if null_value is not None and finding.ci_low is not None and finding.ci_high is not None:
        if finding.ci_low <= null_value <= finding.ci_high:
            numeric_null = True
        else:
            numeric_significant = True

    finding.is_null = bool(null_context or numeric_null)
    finding.is_significant = bool(significant_context or numeric_significant)
    if finding.is_null and finding.is_significant:
        finding.result_label, finding.null_score = "mixed", 0.5
    elif finding.is_null:
        finding.result_label = "null"
        finding.null_score = 0.9 if numeric_null else 0.85
    elif finding.is_significant:
        finding.result_label, finding.null_score = "positive", 0.1
    else:
        finding.result_label, finding.null_score = "no_result_stated", 0.0


def _positive_lexical(sentence: str) -> bool:
    # Mirror weak_classify: drop negated significance clauses before the positive test.
    cleaned = re.sub(
        r"\b(?:not|no)\s+(?:a\s+)?(?:statistically\s+)?significant\w*\s+\w+",
        "",
        sentence,
        flags=re.IGNORECASE,
    )
    return bool(POSITIVE_RE.search(cleaned))


def _terms_in(text: str) -> list[str]:
    return [category for category, pattern in _TERM_RES.items() if pattern.search(text)]


def _sentence_findings(
    sentence: str, section: str, part: str, null_context: bool, significant_context: bool
) -> list[Finding]:
    terms = _terms_in(sentence)
    effects = _effects(sentence)
    intervals = _confidence_intervals(sentence)
    bare_intervals = [
        Match(hit.start(), hit.end(), low=_parse_number(hit.group("low")), high=_parse_number(hit.group("high")))
        for hit in BRACKET_INTERVAL_RE.finditer(sentence)
    ]
    p_values = _p_values(sentence)
    correlations = _correlations(sentence)
    sizes = _sample_sizes(sentence)
    default_n = integer(sizes[0].value) if sizes else None

    null_lexical = bool(NULL_RE.search(sentence)) or bool(NONSIGNIFICANT_RE.search(sentence))
    positive_lexical = _positive_lexical(sentence)
    local_null = null_context or null_lexical
    local_significant = significant_context or positive_lexical

    findings: list[Finding] = []

    def build(anchor: int, base: Finding) -> Finding:
        interval = _nearest(anchor, intervals) or _nearest(anchor, bare_intervals)
        if interval is not None:
            interval.used = True
            base.ci_low, base.ci_high = interval.low, interval.high
            base.ci_level = interval.level if interval.level is not None else 95.0
        p_hit = _nearest(anchor, p_values)
        if p_hit is not None:
            p_hit.used = True
            base.p_value = p_hit.value
            base.p_value_operator = p_hit.operator
            base.p_value_is_exact = p_hit.operator == "="
        base.n = default_n
        # Report every statistic category actually present, from the dictionary
        # scan and from the parsed numbers, not just the dictionary phrases.
        derived = set(base.terms)
        if base.p_value is not None:
            derived.add("p_value")
        if base.ci_low is not None and base.ci_high is not None:
            derived.add("confidence_interval")
        if base.correlation is not None:
            derived.add("correlation")
        if base.estimate is not None and base.effect_type is not None:
            derived.add("effect_size")
        if base.n is not None:
            derived.add("sample_size")
        base.terms = sorted(derived)
        _classify_finding(base, local_null, local_significant)
        return base

    for effect in effects:
        findings.append(
            build(
                effect.start,
                Finding(
                    sentence=sentence,
                    section=section,
                    part=part,
                    terms=terms,
                    estimate=effect.value,
                    effect_type=effect.effect_type,
                    effect_name=effect.effect_name,
                ),
            )
        )
    for correlation in correlations:
        findings.append(
            build(
                correlation.start,
                Finding(
                    sentence=sentence,
                    section=section,
                    part=part,
                    terms=terms,
                    correlation=correlation.value,
                    effect_name=correlation.effect_name,
                ),
            )
        )
    # Confidence intervals and p-values not already attached to an estimate.
    for interval in intervals + bare_intervals:
        if interval.used:
            continue
        findings.append(
            build(
                interval.start,
                Finding(sentence=sentence, section=section, part=part, terms=terms),
            )
        )
    for p_hit in p_values:
        if p_hit.used:
            continue
        findings.append(
            build(
                p_hit.start,
                Finding(sentence=sentence, section=section, part=part, terms=terms),
            )
        )
    # Numberless fallback: keep a sentence only when it *itself* states a null or
    # significant result, so paragraph context alone never invents a finding.
    if not findings and (null_lexical or positive_lexical) and terms:
        finding = Finding(sentence=sentence, section=section, part=part, terms=terms, n=default_n)
        _classify_finding(finding, local_null, local_significant)
        findings.append(finding)
    return findings


def _paragraph_findings(paragraph: Paragraph) -> list[Finding]:
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(paragraph.text) if s.strip()]
    if not sentences:
        return []
    # "Scan the paragraphs around": a null/positive confirmation anywhere in the
    # paragraph colors every statistic reported in it.
    null_context = any(NULL_RE.search(s) or NONSIGNIFICANT_RE.search(s) for s in sentences)
    significant_context = any(_positive_lexical(s) for s in sentences)
    findings: list[Finding] = []
    for sentence in sentences:
        findings.extend(
            _sentence_findings(
                sentence, paragraph.section, paragraph.part, null_context, significant_context
            )
        )
    return findings


def _table_findings(table: Table) -> list[Finding]:
    findings: list[Finding] = []
    context_text = " ".join([table.caption, *(" ".join(row) for row in table.rows)])
    null_context = bool(NULL_RE.search(context_text) or NONSIGNIFICANT_RE.search(context_text))
    significant_context = _positive_lexical(context_text)
    units = [table.caption, *(" ".join(row) for row in table.rows)]
    for unit in units:
        if not unit.strip():
            continue
        findings.extend(
            _sentence_findings(
                unit, table.section or "Table", "table", null_context, significant_context
            )
        )
    return findings


def extract_findings(document: TeiDocument) -> list[Finding]:
    """All statistically relevant findings across body, abstract, tables, appendix."""
    findings: list[Finding] = []
    for paragraph in document.paragraphs:
        findings.extend(_paragraph_findings(paragraph))
    for table in document.tables:
        findings.extend(_table_findings(table))
    return findings


def _completeness(finding: Finding) -> int:
    score = 0
    if finding.estimate is not None and finding.ci_low is not None and finding.ci_high is not None:
        score += 5
    elif finding.ci_low is not None and finding.ci_high is not None:
        score += 2
    if finding.p_value is not None:
        score += 2
    if finding.effect_type is not None:
        score += 2
    if finding.correlation is not None:
        score += 1
    if finding.n is not None:
        score += 1
    section = (finding.section or "").lower()
    if any(word in section for word in ("result", "finding", "conclusion", "outcome")):
        score += 2
    if finding.part == "abstract":
        score += 1
    if finding.part in {"table", "table_caption"}:
        score -= 1
    return score


def summarize(findings: list[Finding]) -> dict:
    """Aggregate document-level result fields from the richest available finding."""
    summary = {
        "result_label": "no_result_stated",
        "null_score": 0.0,
        "evidence_span": "",
        "estimate": None,
        "effect_type": None,
        "ci_low": None,
        "ci_high": None,
        "ci_level": None,
        "p_value": None,
        "p_value_operator": "",
        "p_value_is_exact": None,
        "n": None,
    }
    if not findings:
        return summary
    numeric = [f for f in findings if _completeness(f) > 0]
    primary = max(numeric or findings, key=_completeness)
    null_count = sum(f.is_null and not f.is_significant for f in findings)
    positive_count = sum(f.is_significant and not f.is_null for f in findings)
    if null_count and positive_count:
        label = "mixed"
    elif null_count:
        label = "null"
    elif positive_count:
        label = "positive"
    else:
        label = primary.result_label
    summary.update(
        result_label=label,
        null_score=round(primary.null_score, 3),
        evidence_span=primary.sentence[:600],
        estimate=primary.estimate,
        effect_type=primary.effect_type,
        ci_low=primary.ci_low,
        ci_high=primary.ci_high,
        ci_level=primary.ci_level,
        p_value=primary.p_value,
        p_value_operator=primary.p_value_operator,
        p_value_is_exact=primary.p_value_is_exact,
        n=primary.n,
    )
    return summary


def tei_to_study(xml: str, identifier: str | None = None, *, source: str = "openalex") -> dict:
    """Parse one TEI document into a study record with null-hypothesis evidence."""
    document = parse_tei(xml)
    findings = extract_findings(document)
    resolved_id = identifier or document.doi or document.title[:80] or "tei-unknown"
    study = empty_study(str(resolved_id), source)
    summary = summarize(findings)
    null_findings = [f.to_dict() for f in findings if f.is_null]
    study.update(
        title=document.title,
        abstract=document.abstract,
        url=f"https://doi.org/{document.doi}" if document.doi else "",
        result_label=summary["result_label"],
        null_score=summary["null_score"],
        evidence_span=summary["evidence_span"],
        estimate=summary["estimate"],
        effect_type=summary["effect_type"],
        ci_low=summary["ci_low"],
        ci_high=summary["ci_high"],
        ci_level=summary["ci_level"],
        p_value=summary["p_value"],
        p_value_operator=summary["p_value_operator"],
        p_value_is_exact=summary["p_value_is_exact"],
        n=summary["n"],
        classification_method="tei_lexical_numeric",
        tei_extractor_version=TEI_EXTRACTOR_VERSION,
        tei_findings=[f.to_dict() for f in findings],
        null_findings=null_findings,
        null_evidence_count=len(null_findings),
    )
    return study


def _iter_xml_files(paths: list[Path]):
    for path in paths:
        if path.is_dir():
            yield from sorted(path.rglob("*.xml"))
        else:
            yield path


def extract_tei_files(paths: list[Path], output: Path, *, source: str = "openalex") -> dict:
    """Extract null-hypothesis evidence from many TEI files into one JSONL file."""
    import json

    files = list(_iter_xml_files(paths))
    if not files:
        raise ValueError("No TEI .xml files were found in the supplied inputs")
    if output.exists():
        raise FileExistsError("Output already exists; choose a new path")
    output.parent.mkdir(parents=True, exist_ok=True)
    records = 0
    null_records = 0
    with output.open("w") as handle:
        for path in files:
            study = tei_to_study(path.read_text(encoding="utf-8", errors="replace"), path.stem, source=source)
            handle.write(json.dumps(study, ensure_ascii=False) + "\n")
            records += 1
            if study["null_evidence_count"]:
                null_records += 1
    return {
        "files": len(files),
        "records": records,
        "records_with_null_evidence": null_records,
        "output": str(output),
    }
