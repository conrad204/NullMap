"""Small, explicit source normalization helpers."""

import math
import re
from datetime import date
from typing import Any

NCT_PATTERN = re.compile(r"\bNCT\d{8}\b", re.IGNORECASE)


def number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(str(value).strip().replace(",", "").replace("−", "-"))
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def integer(value: Any) -> int | None:
    result = number(value)
    return int(result) if result is not None and result.is_integer() else None


def normalize_date(value: str | None) -> str | None:
    """Use the LAST possible day for partial completion dates (conservative age rule)."""
    if not value:
        return None
    from calendar import monthrange

    try:
        parts = str(value).split("-")
        year = int(parts[0])
        month = int(parts[1]) if len(parts) > 1 else 12
        day = int(parts[2]) if len(parts) > 2 else monthrange(year, month)[1]
        return date(year, month, day).isoformat()
    except (ValueError, IndexError):
        return None


def pmid(value: Any) -> str | None:
    if not value:
        return None
    match = re.fullmatch(r"(?:https?://(?:www\.)?(?:pubmed\.ncbi\.nlm\.nih\.gov/|ncbi\.nlm\.nih\.gov/pubmed/))?(\d+)/?", str(value).strip())
    return match.group(1) if match else None


def empty_study(identifier: str, source: str) -> dict:
    return {
        "id": identifier, "source": source, "title": "", "abstract": "", "authors": [],
        "venue": "", "url": "", "year": None, "publication_date": None,
        "population": "", "intervention": "", "comparator": "", "outcome": "",
        "outcome_unit": "", "result_label": "no_result_stated", "null_score": None,
        "evidence_span": "", "n": None, "estimate": None, "ci_low": None,
        "ci_high": None, "p_value": None, "effect_type": None, "has_control": None,
        "is_review": False, "is_retracted": False, "pmids": [], "nct_ids": [],
        "referenced_works": [], "result_pmids": [], "has_linked_publication": False,
        "primary_completion_date": None, "enrollment_actual": None,
        "enrollment_planned": None, "overall_status": None, "why_stopped": None,
        "has_results": None, "cited_by_count": 0,
    }
