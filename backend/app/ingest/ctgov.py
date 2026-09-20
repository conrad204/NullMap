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


_CONTROL_TITLE = re.compile(
    r"\b(?:placebo|sham|control|usual care|standard (?:of )?care|no (?:treatment|intervention)|"
    r"vehicle|wait(?:ing)?[- ]?list)\b",
    re.IGNORECASE,
)
_PARTICIPANT_UNITS = {"participants", "subjects", "patients"}
_CONTROL_ARM_TYPES = {"PLACEBO_COMPARATOR", "ACTIVE_COMPARATOR", "SHAM_COMPARATOR",
                      "NO_INTERVENTION"}


def _arm_pair(outcome: dict, selected: list[str],
              arm_types: dict[str, str] | None = None) -> tuple[str, str] | None:
    """(intervention, comparator) group IDs, or None when the orientation is not evident.

    A posted analysis fixes the order. Otherwise exactly one of two groups must be the
    control, by its registered arm type or, failing that, by its title.
    """
    if len(selected) == 2:
        return selected[0], selected[1]
    groups = [group for group in outcome.get("groups", []) if group.get("id")]
    if selected or len(groups) != 2:
        return None
    kinds = [(arm_types or {}).get(" ".join(group.get("title", "").casefold().split()))
             for group in groups]
    controls = [kind in _CONTROL_ARM_TYPES for kind in kinds]
    if None in kinds or sum(controls) != 1 or "EXPERIMENTAL" not in kinds:
        controls = [bool(_CONTROL_TITLE.search(group.get("title", ""))) for group in groups]
    if controls == [False, True]:
        return groups[0]["id"], groups[1]["id"]
    if controls == [True, False]:
        return groups[1]["id"], groups[0]["id"]
    return None


def arm_summary(outcome: dict, selected: list[str],
                arm_types: dict[str, str] | None = None) -> dict:
    """Posted per-arm results for one outcome: means with SDs, or participant counts.

    Only a single class and category is read, so repeated timepoints or multi-level
    categories are never collapsed into one comparison. Medians, geometric and
    model-adjusted means are left alone.
    """
    pair = _arm_pair(outcome, selected, arm_types)
    classes = outcome.get("classes") or []
    if pair is None or len(classes) != 1 or len(classes[0].get("categories") or []) != 1:
        return {}
    sizes: dict = {}
    for denominator in [*(classes[0].get("denoms") or []), *(outcome.get("denoms") or [])]:
        if str(denominator.get("units", "")).lower() in _PARTICIPANT_UNITS:
            sizes = {item.get("groupId"): integer(item.get("value"))
                     for item in denominator.get("counts", [])}
            break
    measured = {item.get("groupId"): item
                for item in classes[0]["categories"][0].get("measurements") or []}
    if any(sizes.get(group) is None or group not in measured for group in pair):
        return {}
    param = str(outcome.get("paramType", "")).upper()
    dispersion = str(outcome.get("dispersionType", "")).upper().replace(" ", "_")
    values = [number(measured[group].get("value")) for group in pair]
    summary = {"n_intervention": sizes[pair[0]], "n_comparator": sizes[pair[1]]}
    if param == "COUNT_OF_PARTICIPANTS":
        events = [integer(measured[group].get("value")) for group in pair]
        if None in events or any(event > sizes[group] for event, group in zip(events, pair)):
            return {}
        return {**summary, "events_intervention": events[0], "events_comparator": events[1]}
    if param != "MEAN" or dispersion not in {"STANDARD_DEVIATION", "STANDARD_ERROR"}:
        return {}
    spreads = [number(measured[group].get("spread")) for group in pair]
    if None in values or None in spreads or any(spread <= 0 for spread in spreads):
        return {}
    if dispersion == "STANDARD_ERROR":
        spreads = [spread * sizes[group] ** 0.5 for spread, group in zip(spreads, pair)]
    return {**summary, "mean_intervention": values[0], "mean_comparator": values[1],
            "sd_intervention": spreads[0], "sd_comparator": spreads[1]}


def _analysis_row(outcome: dict, analysis: dict, outcome_index: int, index: int) -> dict:
    groups = {group["id"]: group for group in outcome.get("groups", []) if group.get("id")}
    selected = list(dict.fromkeys(analysis.get("groupIds") or []))
    selected_groups = [groups[group_id] for group_id in selected if group_id in groups]
    p_value, operator = parse_p_value(analysis.get("pValue"))
    ci_percent = number(analysis.get("ciPctValue"))
    denominators = None
    for denominator in outcome.get("denoms", []):
        if str(denominator.get("units", "")).lower() not in _PARTICIPANT_UNITS:
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
    control_types = _CONTROL_ARM_TYPES
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
    primaries = [(index, outcome) for index, outcome in enumerate(measures)
                 if outcome.get("type") == "PRIMARY"]
    if selected:
        # Arm-level numbers must describe the same outcome and arms as the chosen analysis.
        primaries = [(index, outcome) for index, outcome in primaries
                     if index == selected["outcome_index"]]
    arm_types = {" ".join(arm.get("label", "").casefold().split()): arm.get("type")
                 for arm in arms if arm.get("label")}
    for index, outcome in primaries:
        summary = arm_summary(outcome, selected["analysis_group_ids"] if selected else [],
                              arm_types)
        if not summary:
            continue
        study.update(summary)
        if not selected:
            titles = {group.get("id"): group.get("title", "") for group in outcome.get("groups", [])}
            pair = _arm_pair(outcome, [], arm_types)
            study.update({
                "outcome": outcome.get("title", ""), "outcome_unit": outcome.get("unitOfMeasure", ""),
                "intervention": titles.get(pair[0]) or study["intervention"],
                "comparator": titles.get(pair[1]) or study["comparator"],
                "n": summary["n_intervention"] + summary["n_comparator"], "has_control": True,
                "numeric_source": f"resultsSection.outcomeMeasuresModule.outcomeMeasures[{index}]",
                "selection_note": "First primary outcome posting two-arm summary results; "
                                  "no comparative analysis was posted.",
            })
        break
    return study
