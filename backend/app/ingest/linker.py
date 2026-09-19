"""Deduplicate PMID/NCT links while preferring registry primary-outcome numbers."""

from collections import defaultdict

NUMERIC_FIELDS = {
    "n", "estimate", "ci_low", "ci_high", "p_value", "effect_type", "ci_level", "ci_sides",
    "p_value_operator", "p_value_raw", "effect_type_raw", "statistical_method", "numeric_source",
    "outcome", "outcome_unit", "intervention", "comparator", "analysis_group_ids", "evidence_tier",
}
UNION_FIELDS = {"pmids", "nct_ids", "result_pmids", "referenced_works"}


def link_studies(studies: list[dict]) -> list[dict]:
    """Merge papers into a canonical trial and remove duplicate source records.

    A paper can mention several trials. Such a paper is attached to each trial,
    without collapsing distinct NCT IDs into one trial; the publication row is
    removed. Reviews are never proof that an individual trial reported results.
    """
    unique = {study["id"]: dict(study) for study in studies}
    trials = {identifier: row for identifier, row in unique.items()
              if row.get("source") in {"ctgov", "merged"}}
    papers = {identifier: row for identifier, row in unique.items()
              if row.get("source") == "openalex"}
    by_pmid: dict[str, set[str]] = defaultdict(set)
    by_nct: dict[str, set[str]] = defaultdict(set)
    for identifier, trial in trials.items():
        for value in trial.get("result_pmids", []):
            by_pmid[str(value)].add(identifier)
        for value in trial.get("nct_ids", []):
            by_nct[value].add(identifier)
    consumed: set[str] = set()
    for paper_id, paper in papers.items():
        if paper.get("is_review"):
            continue
        targets: set[str] = set()
        for value in paper.get("pmids", []):
            targets.update(by_pmid.get(str(value), set()))
        reports_outcome = paper.get("result_label") in {"positive", "null", "mixed"} or any(
            paper.get(key) is not None for key in ("estimate", "ci_low", "p_value")
        )
        # NCT mentions in protocols/background are not proof that results were published.
        if reports_outcome:
            for value in paper.get("nct_ids", []):
                targets.update(by_nct.get(value, set()))
        for target in sorted(targets):
            trial = trials[target]
            registry_numbers = {key: trial[key] for key in NUMERIC_FIELDS if trial.get(key) is not None}
            registry_has_effect = any(trial.get(key) is not None for key in ("estimate", "ci_low", "p_value"))
            if registry_has_effect:
                registry_numbers["evidence_span"] = trial.get("evidence_span", "")
            trial.setdefault("linked_papers", []).append({
                "id": paper_id, "title": paper.get("title"), "url": paper.get("url"),
                "result_label": paper.get("result_label"), "evidence_span": paper.get("evidence_span"),
            })
            # The first linked paper supplies searchable literature text, never registry effects.
            if trial.get("source") != "merged":
                trial["registry_title"] = trial.get("title", "")
                trial["registry_abstract"] = trial.get("abstract", "")
                trial["registry_url"] = trial.get("url", "")
                for key in ("title", "abstract", "authors", "venue", "embedding", "embedding_model",
                            "result_label", "null_score", "evidence_span", "classification_method"):
                    if key in paper:
                        trial[key] = paper[key]
                if not registry_has_effect:
                    for key in NUMERIC_FIELDS:
                        if paper.get(key) is not None:
                            trial[key] = paper[key]
                trial.update(registry_numbers)
            for key in UNION_FIELDS:
                # Only the trial's own NCT ID belongs to this canonical row.
                if key != "nct_ids":
                    trial[key] = sorted(set(trial.get(key, [])) | set(paper.get(key, [])))
            trial["is_retracted"] = bool(trial.get("is_retracted") or paper.get("is_retracted"))
            trial["source"] = "merged"
            trial["has_linked_publication"] = True
            trial["paper_result_label"] = paper.get("result_label", "no_result_stated")
            # Disagreement is descriptive; statistical equivalence is determined later.
            p = trial.get("p_value")
            null_registry = (p is not None and p >= 0.05 and
                             trial.get("p_value_operator", "=") in {"=", ">", ">="})
            trial["possible_abstract_spin"] = bool(null_registry and paper.get("result_label") == "positive")
        if targets:
            consumed.add(paper_id)
    return [trials.get(identifier, row) for identifier, row in unique.items() if identifier not in consumed]
