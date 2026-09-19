"""ClinicalTrials.gov API v2 normalization, verified against NCT01169259.

One row is one registry study. Its canonical effect is the first comparative
primary analysis with an interpretable effect scale; all primary analyses are
retained so that endpoint selection and factorial comparisons are auditable.
"""

import re

from app.ingest.common import empty_study, integer, normalize_date, number, pmid


def parse_p_value(value) -> tuple[float | None, str | None]:
    if value is None:
        return None, None
    match = re.fullmatch(r"\s*(<=|>=|<|>|=|≤|≥)?\s*([\d.eE+\-]+)\s*", str(value))
    if not match:
        return None, None
    parsed = number(match.group(2))
    if parsed is None or not 0 <= parsed <= 1:
        return None, None
    return parsed, {"≤": "<=", "≥": ">="}.get(match.group(1), match.group(1) or "=")


def effect_type(value: str | None) -> str | None:
    cleaned = re.sub(r"[^a-z0-9]", "", (value or "").lower())
    aliases = {
        "hazardratio": "HR", "hazardratiohr": "HR", "hr": "HR",
        "oddsratio": "OR", "oddsratioor": "OR", "or": "OR",
        "riskratio": "RR", "relativerisk": "RR", "riskratiorr": "RR", "rr": "RR",
        "standardizedmeandifference": "SMD", "standardisedmeandifference": "SMD",
        "cohensd": "SMD", "hedgesg": "SMD", "smd": "SMD",
        "meandifference": "MD", "differenceofmeans": "MD", "differenceinmeans": "MD",
        "meandifferencenet": "MD", "md": "MD",
        "riskdifference": "RD", "differenceinproportions": "RD",
    }
    return aliases.get(cleaned)


def _analysis_row(outcome: dict, analysis: dict, outcome_index: int, index: int) -> dict:
    groups = {group["id"]: group for group in outcome.get("groups", []) if group.get("id")}
    selected = list(dict.fromkeys(analysis.get("groupIds") or []))
    selected_groups = [groups[group_id] for group_id in selected if group_id in groups]
    p_value, operator = parse_p_value(analysis.get("pValue"))
    ci_percent = number(analysis.get("ciPctValue"))
    denominators = None
    for denominator in outcome.get("denoms", []):
        if str(denominator.get("units", "")).lower() not in {"participants", "subjects", "patients"}:
            continue
        counts = {item.get("groupId"): integer(item.get("value"))
                  for item in denominator.get("counts", [])}
        if selected and all(counts.get(group_id) is not None for group_id in selected):
            denominators = sum(counts[group_id] for group_id in selected)
            break
    return {
        "outcome": outcome.get("title", ""), "outcome_unit": outcome.get("unitOfMeasure", ""),
        "outcome_index": outcome_index, "analysis_index": index, "outcome_type": "PRIMARY",
        "time_frame": outcome.get("timeFrame", ""),
        "estimate": number(analysis.get("paramValue")),
        "ci_low": number(analysis.get("ciLowerLimit")),
        "ci_high": number(analysis.get("ciUpperLimit")),
        "ci_level": ci_percent / 100 if ci_percent is not None and 0 < ci_percent < 100 else None,
        "ci_sides": analysis.get("ciNumSides"),
        "p_value": p_value, "p_value_operator": operator,
        "p_value_raw": analysis.get("pValue"),
        "effect_type": effect_type(analysis.get("paramType")),
        "effect_type_raw": analysis.get("paramType"),
        "statistical_method": analysis.get("statisticalMethod"),
        "analysis_group_ids": selected, "n": denominators,
        "intervention": selected_groups[0].get("title", "") if selected_groups else "",
        "comparator": " vs ".join(group.get("title", "") for group in selected_groups[1:]),
        "analysis_is_comparative": len(selected) >= 2,
        "non_inferiority_type": analysis.get("nonInferiorityType"),
    }


def flatten_trial(record: dict) -> dict:
    protocol = record.get("protocolSection") or {}
    identity = protocol.get("identificationModule") or {}
    nct_id = identity.get("nctId", "").upper()
    if not re.fullmatch(r"NCT\d{8}", nct_id):
        raise ValueError("ClinicalTrials.gov record requires an NCT identifier")
    study = empty_study(nct_id, "ctgov")
    status = protocol.get("statusModule") or {}
    design = protocol.get("designModule") or {}
    enrollment = design.get("enrollmentInfo") or {}
    arms_module = protocol.get("armsInterventionsModule") or {}
    arms = arms_module.get("armGroups") or []
    interventions = arms_module.get("interventions") or []
    description = protocol.get("descriptionModule") or {}
    conditions = (protocol.get("conditionsModule") or {}).get("conditions") or []
    eligibility = protocol.get("eligibilityModule") or {}
    primary = (protocol.get("outcomesModule") or {}).get("primaryOutcomes") or []
    references = (protocol.get("referencesModule") or {}).get("references") or []
    result_pmids = sorted({p for ref in references if ref.get("type") == "RESULT"
                           if (p := pmid(ref.get("pmid")))})
    completion = normalize_date((status.get("primaryCompletionDateStruct") or {}).get("date"))
    year = int(completion[:4]) if completion else None
    arm_types = {arm.get("type") for arm in arms}
    control_types = {"PLACEBO_COMPARATOR", "ACTIVE_COMPARATOR", "SHAM_COMPARATOR", "NO_INTERVENTION"}
    has_control = True if arm_types & control_types else None
    intervention_model = (design.get("designInfo") or {}).get("interventionModel")
    if intervention_model == "SINGLE_GROUP" or (len(arms) == 1 and design.get("studyType") == "INTERVENTIONAL"):
        has_control = False
    enrollment_count = integer(enrollment.get("count"))
    study.update({
        "title": identity.get("briefTitle") or identity.get("officialTitle") or nct_id,
        "abstract": description.get("briefSummary") or description.get("detailedDescription") or "",
        "venue": "ClinicalTrials.gov", "url": f"https://clinicaltrials.gov/study/{nct_id}",
        "year": year, "publication_date": f"{year:04d}-01-01" if year else None,
        "population": "; ".join(conditions),
        "eligibility": eligibility.get("eligibilityCriteria", ""),
        "intervention": "; ".join(item.get("name", "") for item in interventions),
        "comparator": "; ".join(arm.get("label", "") for arm in arms if arm.get("type") in control_types),
        "outcome": "; ".join(item.get("measure", "") for item in primary),
        "nct_ids": [nct_id], "pmids": result_pmids, "result_pmids": result_pmids,
        # A registry RESULT reference is evidence of reporting even if its paper is outside our index.
        "has_linked_publication": bool(result_pmids),
        "overall_status": status.get("overallStatus"), "why_stopped": status.get("whyStopped"),
        "primary_completion_date": completion, "primary_completion_date_raw":
            (status.get("primaryCompletionDateStruct") or {}).get("date"),
        "has_results": record.get("hasResults") if isinstance(record.get("hasResults"), bool) else None,
        "enrollment_actual": enrollment_count if enrollment.get("type") == "ACTUAL" else None,
        "enrollment_planned": enrollment_count if enrollment.get("type") == "ESTIMATED" else None,
        "n": enrollment_count if enrollment.get("type") == "ACTUAL" else None,
        "has_control": has_control, "study_design": design.get("studyType"),
        "result_label": "no_result_stated", "classification_method": "registry",
    })
    measures = ((record.get("resultsSection") or {}).get("outcomeMeasuresModule") or {}).get("outcomeMeasures") or []
    analyses = [_analysis_row(outcome, analysis, outcome_index, index)
                for outcome_index, outcome in enumerate(measures)
                if outcome.get("type") == "PRIMARY"
                for index, analysis in enumerate(outcome.get("analyses") or [])]
    study["registry_analyses"] = analyses
    supported = [row for row in analyses if row["analysis_is_comparative"] and row["effect_type"]
                 and any(row.get(key) is not None for key in ("estimate", "ci_low", "p_value"))]
    comparative = [row for row in analyses if row["analysis_is_comparative"]]
    selected = next(iter(supported or comparative), None)
    if selected:
        for key, value in selected.items():
            if key in {"intervention", "comparator", "n"} and value in (None, ""):
                continue
            study[key] = value
        # Registry numbers retain an exact JSON source path rather than a fabricated quote.
        study["numeric_source"] = ("resultsSection.outcomeMeasuresModule.outcomeMeasures"
                                   f"[{selected['outcome_index']}].analyses[{selected['analysis_index']}]")
        study["evidence_span"] = ""
        study["has_control"] = True
        study["evidence_tier"] = "structured"
        study["selection_note"] = ("First comparative primary outcome with a supported effect scale; "
                                   "other primary analyses are retained in registry_analyses.")
    return study
