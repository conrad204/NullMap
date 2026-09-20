from datetime import date
from math import exp, log, sqrt
from statistics import NormalDist

import numpy as np
import pytest

from app.statistics import analyze_studies, assign_bucket, derive_effects, stored_analysis


def study(sid="a", estimate=0.02, low=-0.1, high=0.14, **kwargs):
    return {
        "id": sid,
        "effect_type": "SMD",
        "estimate": estimate,
        "ci_low": low,
        "ci_high": high,
        "outcome": "Depression severity",
        "intervention": "Vitamin D",
        "comparator": "Placebo",
        **kwargs,
    }


def plan(**kwargs):
    return {
        "sesoi": 0.2,
        "effectType": "SMD",
        "plannedN": 200,
        "alpha": 0.05,
        "valueSuccess": 100,
        "valueNull": 20,
        "studyCost": 30,
        **kwargs,
    }


def registry(**kwargs):
    return {
        "source": "ctgov",
        "nct_ids": ["NCT00000001"],
        "overall_status": "COMPLETED",
        "primary_completion_date": "2025-09-18",
        "has_results": False,
        "has_linked_publication": False,
        "result_pmids": [],
        **kwargs,
    }


def test_narrow_null_is_equivalent_but_wide_null_is_inconclusive():
    narrow = assign_bucket(study())
    wide = assign_bucket(study(low=-0.6, high=0.64))
    assert narrow["bucket"] == "credible_null"
    assert narrow["evidence_tier"] == "numeric"
    assert wide["bucket"] == "inconclusive"
    assert narrow["mde"] < wide["mde"]


@pytest.mark.parametrize("low,high", [(-0.2, 0.15), (-0.1, 0.2), (-0.2, 0.2)])
def test_equivalence_bounds_are_strict(low, high):
    assert assign_bucket(study(low=low, high=high))["bucket"] == "inconclusive"


def test_significant_but_trivial_is_credible_null():
    result = assign_bucket(study(estimate=0.08, low=0.02, high=0.14))
    assert result["bucket"] == "credible_null"
    assert result["significant_but_trivial"] is True


@pytest.mark.parametrize("estimate,low,high", [(0.4, 0.1, 0.7), (-0.4, -0.7, -0.1)])
def test_effect_can_be_in_either_direction(estimate, low, high):
    result = assign_bucket(study(estimate=estimate, low=low, high=high))
    assert result["bucket"] == "effect"
    assert result["significant_but_trivial"] is False


def test_significant_small_point_with_meaningful_ci_tail_remains_inconclusive():
    assert assign_bucket(study(estimate=0.15, low=0.01, high=0.29))["bucket"] == "inconclusive"


def test_text_null_never_becomes_credible_null():
    result = assign_bucket({"result_label": "null", "n": 10_000})
    # A reported null is surfaced as such, but is never evidence of equivalence.
    assert result["bucket"] == "reported_null"
    assert "cannot establish absence" in result["rationale"]
    assert result["evidence_tier"] == "text_only"
    assert result["mde"] is None
    positive = assign_bucket({"result_label": "positive"})
    assert positive["bucket"] == "effect"
    assert "unverified" in positive["rationale"]


@pytest.mark.parametrize(
    "metadata",
    [
        {"is_retracted": True},
        {"has_control": False},
        {"overall_status": "TERMINATED", "why_stopped": "Funding exhausted"},
        {"overall_status": "WITHDRAWN"},
        {"overall_status": "SUSPENDED"},
        {"enrollment_actual": 49, "enrollment_planned": 100},
        {"enrollment_actual": 0, "enrollment_planned": 100},
    ],
)
def test_methodological_failure_overrides_positive_effect(metadata):
    assert assign_bucket(study(estimate=0.5, low=0.3, high=0.7, **metadata))["bucket"] == "failed"


def test_unknown_or_half_enrollment_is_not_failed():
    assert assign_bucket(study(enrollment_actual=50, enrollment_planned=100))["bucket"] != "failed"
    assert (
        assign_bucket(study(enrollment_actual=None, enrollment_planned=100))["bucket"] != "failed"
    )


def test_completed_registry_requires_more_than_twelve_calendar_months():
    today = date(2026, 9, 19)
    assert assign_bucket(registry(), today=today)["bucket"] == "unreported"
    exact = registry(primary_completion_date="2025-09-19")
    assert assign_bucket(exact, today=today)["bucket"] == "inconclusive"
    leap = registry(primary_completion_date="2024-02-29")
    assert assign_bucket(leap, today=date(2025, 2, 28))["bucket"] == "inconclusive"
    assert assign_bucket(leap, today=date(2025, 3, 1))["bucket"] == "unreported"


@pytest.mark.parametrize(
    "metadata",
    [
        {"has_results": True},
        {"has_results": None},
        {"has_linked_publication": True},
        {"has_linked_publication": None},
        {"result_pmids": ["12345"]},
        {"source": "merged"},
        {"primary_completion_date": None},
        {"primary_completion_date": "nonsense"},
        {"primary_completion_date": "2025-09"},
    ],
)
def test_unknown_dates_or_reporting_metadata_do_not_assert_unreported(metadata):
    assert assign_bucket(registry(**metadata), today=date(2026, 9, 19))["bucket"] != "unreported"


def test_ratio_studies_and_sesoi_use_compatible_log_scales():
    ratio = study(estimate=1.02, low=0.9, high=1.1, effect_type="OR")
    result = assign_bucket(ratio, sesoi=0.2, effect_type="logOR")
    assert result["bucket"] == "credible_null"
    assert result["analysis_effect_type"] == "logOR"
    assert result["analysis_estimate"] == pytest.approx(log(1.02))
    assert result["analysis_ci"] == pytest.approx([log(0.9), log(1.1)])
    assert assign_bucket(ratio, sesoi=exp(0.2), effect_type="OR")["bucket"] == "credible_null"
    assert assign_bucket(ratio, sesoi=0.2, effect_type="OR")["bucket"] == "inconclusive"
    assert assign_bucket(ratio, sesoi=0.2, effect_type="SMD")["bucket"] == "inconclusive"


def test_ratio_null_is_one_and_zero_ratio_bounds_are_invalid():
    effect = study(estimate=0.7, low=0.55, high=0.9, effect_type="RR")
    assert assign_bucket(effect, effect_type="logRR")["bucket"] == "effect"
    bad = study(estimate=0.7, low=0, high=0.9, effect_type="RR")
    assert assign_bucket(bad, effect_type="logRR")["analysis_ci"] is None


def test_estimate_and_exact_p_can_reconstruct_but_p_and_n_alone_cannot():
    p_only = {"effect_type": "SMD", "n": 400, "p_value": 0.5, "result_label": "null"}
    result = assign_bucket(p_only)
    assert result["analysis_se"] is None
    assert result["bucket"] == "reported_null"
    reconstructed = assign_bucket({**p_only, "estimate": 0.1, "p_value": 0.05})
    assert reconstructed["evidence_tier"] == "reconstructed"
    assert reconstructed["analysis_se"] == pytest.approx(0.1 / 1.95996398454)
    assert "Wald" in " ".join(reconstructed["numeric_notes"])


@pytest.mark.parametrize(
    "p,metadata",
    [
        ("<0.001", {}),
        (0, {}),
        (1, {}),
        (float("nan"), {}),
        (0.001, {"p_value_operator": "<"}),
        (0.001, {"p_value_operator": "<="}),
        (0.001, {"p_value_operator": ">"}),
        (0.001, {"p_value_is_exact": False}),
    ],
)
def test_censored_or_invalid_p_is_not_used_as_exact(p, metadata):
    result = assign_bucket({"effect_type": "SMD", "estimate": 0.1, "p_value": p, **metadata})
    assert result["analysis_se"] is None


@pytest.mark.parametrize(
    "metadata",
    [
        {"estimate": 0.5, "ci_low": -0.1, "ci_high": 0.1},
        {"ci_low": 0.1, "ci_high": -0.1},
        {"ci_low": 0, "ci_high": 0},
        {"ci_low": float("nan")},
        {"ci_high": float("inf")},
        {"ci_sides": "ONE_SIDED"},
        {"ci_level": 200},
    ],
)
def test_bad_or_one_sided_intervals_do_not_establish_equivalence(metadata):
    result = assign_bucket(study(**metadata))
    assert result["bucket"] != "credible_null"
    assert result["analysis_ci"] is None


def test_ninety_percent_ci_is_widened_before_equivalence_decision():
    result = assign_bucket(study(estimate=0, low=-0.18, high=0.18, ci_level=0.9))
    assert result["analysis_ci_high"] > 0.2
    assert result["bucket"] == "inconclusive"
    assert result["evidence_tier"] == "reconstructed"


def test_unknown_scale_is_not_assumed_to_be_smd():
    assert assign_bucket(study(effect_type=None))["analysis_se"] is None


def test_missing_point_estimate_can_establish_equivalence_but_not_be_pooled():
    result = assign_bucket(study(estimate=None))
    assert result["bucket"] == "credible_null"
    report = analyze_studies([study(str(i), estimate=None) for i in range(3)], plan())
    assert report["pools"] == []


def test_pooling_identical_studies_clamps_negative_dl_tau_squared():
    studies = [study(str(i), estimate=0.1, low=-0.1, high=0.3) for i in range(3)]
    result = analyze_studies(studies, plan())
    pool = result["pools"][0]
    assert pool["k"] == 3
    assert pool["tau2"] == 0
    assert pool["estimate"] == pytest.approx(0.1)
    single_se = assign_bucket(studies[0])["analysis_se"]
    assert pool["se"] == pytest.approx(single_se / sqrt(3))
    assert all(np.isfinite(pool["ci"]))


def test_heterogeneous_opposite_effects_increase_uncertainty():
    studies = [
        study(str(i), estimate=e, low=e - 0.1, high=e + 0.1) for i, e in enumerate([-0.5, 0, 0.5])
    ]
    pool = analyze_studies(studies, plan())["pools"][0]
    assert pool["estimate"] == pytest.approx(0, abs=1e-12)
    assert pool["tau2"] > 0.2
    assert pool["ci"][0] < -0.5 < 0.5 < pool["ci"][1]


def test_log_ratio_and_already_logged_estimates_can_pool():
    studies = [study("raw", estimate=1.1, low=0.9, high=1.3, effect_type="OR")]
    studies += [
        study(str(i), estimate=log(1.1), low=log(0.9), high=log(1.3), effect_type="logOR")
        for i in range(2)
    ]
    result = analyze_studies(studies, plan(effectType="logOR", baselineRisk=0.2))
    assert result["pools"][0]["effectType"] == "logOR"
    assert result["pools"][0]["estimate"] == pytest.approx(log(1.1))
    assert result["assurance"] is not None


@pytest.mark.parametrize(
    "different",
    [
        {"outcome": "Cognitive function"},
        {"outcome_unit": "other scale"},
        {"effect_type": "MD"},
        {"intervention": "Placebo", "comparator": "Vitamin D"},
        {"effect_direction": "lower is better"},
    ],
)
def test_incompatible_third_study_does_not_create_pool(different):
    studies = [study("a"), study("b"), study("c", **different)]
    assert analyze_studies(studies, plan())["pools"] == []


def test_missing_outcomes_md_units_and_reviews_are_excluded():
    for metadata in (
        {"outcome": None},
        {"effect_type": "MD", "outcome_unit": None},
        {"is_review": True},
        {"is_retracted": True},
        {"has_control": False},
    ):
        studies = [study(str(i), **metadata) for i in range(3)]
        assert analyze_studies(studies, plan())["pools"] == []


def test_duplicate_registry_and_paper_do_not_double_count_patient_samples():
    studies = [study("a", nct_ids=["NCT00000001"]), study("b", nct_ids=["NCT00000001"]), study("c")]
    result = analyze_studies(studies, plan())
    assert result["pools"] == []
    assert any("Duplicate" in warning for warning in result["warnings"])


def test_multiple_outcomes_require_an_explicit_selection_for_assurance():
    studies = [study(str(i)) for i in range(3)]
    studies += [study(str(i + 3), outcome="Cognitive function") for i in range(3)]
    result = analyze_studies(studies, plan())
    assert len(result["pools"]) == 2
    assert result["assurance"] is None
    selected = analyze_studies(studies, plan(outcome="Depression severity"))
    assert selected["assurance"] is not None
    assert set(selected["selectedPoolStudyIds"]) == {"0", "1", "2"}


def test_planned_mde_uses_total_n_two_equal_arms():
    result = analyze_studies([], plan(plannedN=400))
    assert result["plannedMde"] == pytest.approx(0.28015852184)
    assert result["plannedNullBucket"] == "credible_null"
    assert analyze_studies([], plan(plannedN=100))["plannedNullBucket"] == "inconclusive"
    assert result["assurance"] is None
    assert result["expectedValue"] is None


@pytest.mark.parametrize(
    "effect_type,kwargs",
    [
        ("MD", {}),
        ("logOR", {}),
        ("logRR", {"baselineRisk": 1}),
        ("logHR", {}),
    ],
)
def test_planning_needs_outcome_specific_design_information(effect_type, kwargs):
    result = analyze_studies([], plan(effectType=effect_type, **kwargs))
    assert result["plannedMde"] is None
    assert result["requiredN"] is None
    assert result["assurance"] is None


def test_md_uses_supplied_outcome_standard_deviation():
    smd = analyze_studies([], plan())
    md = analyze_studies([], plan(effectType="MD", outcomeSd=7))
    assert md["plannedMde"] == pytest.approx(7 * smd["plannedMde"])


def test_analytic_assurance_agrees_with_expected_power_quadrature():
    studies = [
        study(str(i), estimate=e, low=e - 0.15, high=e + 0.15)
        for i, e in enumerate([0.1, 0.15, 0.3])
    ]
    result = analyze_studies(studies, plan())
    pool = result["pools"][0]
    # Independently integrate conditional two-sided power with Gauss-Hermite nodes.
    nodes, weights = np.polynomial.hermite.hermgauss(80)
    effects = pool["estimate"] + sqrt(2 * (pool["tau2"] + pool["se"] ** 2)) * nodes
    normal = NormalDist()
    se = 2 / sqrt(200)
    z = normal.inv_cdf(0.975)
    powers = [normal.cdf(-z - effect / se) + normal.cdf(effect / se - z) for effect in effects]
    expected = float(np.dot(weights, powers) / sqrt(np.pi))
    assert result["assurance"] == pytest.approx(expected, abs=1e-12)
    assert result["expectedValue"] == pytest.approx(expected * 100 + (1 - expected) * 20 - 30)
    assert result == analyze_studies(studies, plan())


def test_required_n_is_smallest_even_total_reaching_eighty_percent_assurance():
    studies = [study(str(i), estimate=0.3, low=0.1, high=0.5) for i in range(3)]
    required = analyze_studies(studies, plan())["requiredN"]
    assert required is not None and required % 2 == 0
    assert analyze_studies(studies, plan(plannedN=required))["assurance"] >= 0.8
    assert analyze_studies(studies, plan(plannedN=required - 2))["assurance"] < 0.8


def test_success_includes_effects_in_unfavorable_direction_and_ev_keeps_units():
    studies = [study(str(i), estimate=-0.5, low=-0.6, high=-0.4) for i in range(3)]
    result = analyze_studies(studies, plan(valueSuccess=1000, valueNull=-10, studyCost=123))
    assert result["assurance"] > 0.8
    assert "either direction" in result["assuranceDefinition"]
    assert result["expectedValue"] == pytest.approx(
        result["assurance"] * 1000 + (1 - result["assurance"]) * -10 - 123
    )


def test_full_match_file_drawer_overrides_page_counts_and_recomputes_share():
    result = analyze_studies([], plan(), {"completed": 100, "unreported": 30, "share": 0.9})
    assert result["fileDrawer"] == {"completed": 100, "unreported": 30, "share": 0.3}
    assert any("publication bias" in warning for warning in result["warnings"])
    assert (
        analyze_studies([], plan(), {"completed": 0, "unreported": 0})["fileDrawer"]["share"]
        is None
    )


def test_file_drawer_local_fallback_is_explicit():
    result = analyze_studies([registry(primary_completion_date="2020-01-01")], plan())
    assert result["fileDrawer"] == {"completed": 1, "unreported": 1, "overdue": 1, "share": 1.0}
    assert any("supplied studies only" in warning for warning in result["warnings"])


@pytest.mark.parametrize(
    "settings", [{"plannedN": 0}, {"plannedN": 3.5}, {"alpha": 0}, {"alpha": 1}]
)
def test_invalid_plan_does_not_produce_fabricated_statistics(settings):
    result = analyze_studies([study(str(i)) for i in range(3)], plan(**settings))
    assert result["plannedMde"] is None
    assert result["assurance"] is None


def test_input_documents_are_not_mutated():
    document = study()
    original = dict(document)
    assign_bucket(document)
    analyze_studies([document], plan())
    assert document == original


@pytest.mark.parametrize(
    "metadata",
    [
        {"estimate": 0.1, "p_value": 5e-324},
        {"estimate": 0.1, "ci_low": 0, "ci_high": 0.2, "ci_level": 1e-100},
        {"estimate": 0, "ci_low": -1e-200, "ci_high": 1e-200},
    ],
)
def test_extreme_numeric_input_fails_closed_without_crashing(metadata):
    result = assign_bucket({"effect_type": "SMD", **metadata})
    assert result["analysis_se"] is None


def test_very_small_alpha_and_large_sd_do_not_crash_planning():
    result = analyze_studies([], plan(alpha=1e-100))
    assert np.isfinite(result["plannedMde"])
    result = analyze_studies([], plan(effectType="MD", outcomeSd=1e308))
    assert result["plannedMde"] is None


def test_independent_registry_trials_sharing_a_publication_are_not_collapsed():
    trials = [
        study(str(i), source="merged", nct_ids=[f"NCT{i:08d}"], pmids=["same-publication"])
        for i in range(3)
    ]
    assert analyze_studies(trials, plan())["pools"][0]["k"] == 3


def test_recent_missing_results_count_in_file_drawer_but_are_not_overdue():
    result = analyze_studies([registry(primary_completion_date="2099-01-01")], plan())
    assert result["fileDrawer"] == {"completed": 1, "unreported": 1, "overdue": 0, "share": 1.0}


def test_review_without_control_arm_is_discovery_evidence_not_a_failed_trial():
    review = study(is_review=True, has_control=False)
    assert assign_bucket(review)["bucket"] == "inconclusive"
    assert "Review used for discovery" in assign_bucket(review)["rationale"]
    assert assign_bucket({**review, "is_retracted": True})["bucket"] == "failed"


ARMS = {
    "mean_intervention": 12.0, "mean_comparator": 10.0, "sd_intervention": 4.0,
    "sd_comparator": 4.0, "n_intervention": 50, "n_comparator": 50,
}


def test_arm_level_means_give_hedges_g_and_mean_difference():
    effects = derive_effects(ARMS)
    correction = 1 - 3 / (4 * 98 - 1)
    assert effects["SMD"]["estimate"] == pytest.approx(0.5 * correction)
    assert effects["SMD"]["se"] == pytest.approx(correction * sqrt(100 / 2500 + 0.25 / 200))
    assert effects["MD"]["estimate"] == pytest.approx(2.0)
    assert effects["MD"]["se"] == pytest.approx(sqrt(16 / 50 + 16 / 50))
    result = assign_bucket({**ARMS, "outcome": "Pain"})
    assert result["evidence_tier"] == "derived" and result["bucket"] == "effect"
    assert any("Hedges" in note for note in result["numeric_notes"])


def test_arm_level_events_give_log_ratios_with_continuity_correction_only_when_needed():
    arms = {"events_intervention": 20, "events_comparator": 40,
            "n_intervention": 100, "n_comparator": 100}
    effects = derive_effects(arms)
    assert effects["logOR"]["estimate"] == pytest.approx(log((20 * 60) / (80 * 40)))
    assert effects["logOR"]["se"] == pytest.approx(sqrt(1 / 20 + 1 / 80 + 1 / 40 + 1 / 60))
    assert effects["logRR"]["estimate"] == pytest.approx(log(0.2 / 0.4))
    assert set(effects) == {"logOR", "logRR"}
    empty_cell = derive_effects({**arms, "events_intervention": 0})
    assert empty_cell["logOR"]["estimate"] == pytest.approx(log((0.5 * 60.5) / (100.5 * 40.5)))
    assert "continuity" in empty_cell["logOR"]["note"]


@pytest.mark.parametrize(
    "arms",
    [
        {**ARMS, "sd_comparator": None},
        {**ARMS, "sd_intervention": 0},
        {**ARMS, "n_comparator": 1},
        {**ARMS, "n_intervention": 50.5},
        {"events_intervention": 0, "events_comparator": 0, "n_intervention": 9, "n_comparator": 9},
        {"events_intervention": 9, "events_comparator": 9, "n_intervention": 9, "n_comparator": 9},
        {"events_intervention": 12, "events_comparator": 3, "n_intervention": 9, "n_comparator": 9},
        {"mean_intervention": 1.0, "mean_comparator": 2.0, "n_intervention": 9, "n_comparator": 9},
        # 96 units apart with "SDs" of 1.0 and 2.3: a standard error reported as an SD.
        {"mean_intervention": 202.1, "mean_comparator": 298.0, "sd_intervention": 1.0,
         "sd_comparator": 2.3, "n_intervention": 21, "n_comparator": 21},
    ],
)
def test_incomplete_or_impossible_arm_summaries_derive_nothing(arms):
    assert derive_effects(arms) == {}
    assert assign_bucket(arms)["evidence_tier"] == "text_only"


def test_reported_interval_on_the_requested_scale_is_never_replaced_by_a_derived_one():
    trial = {"effect_type": "HR", "estimate": 0.96, "ci_low": 0.88, "ci_high": 1.06,
             "events_intervention": 793, "events_comparator": 824,
             "n_intervention": 12927, "n_comparator": 12944}
    reported = assign_bucket(trial, 1.25, "HR")
    assert reported["evidence_tier"] == "numeric" and reported["analysis_effect_type"] == "logHR"
    assert reported["analysis_estimate"] == pytest.approx(log(0.96))
    derived = assign_bucket(trial, 1.25, "OR")
    assert derived["evidence_tier"] == "derived" and derived["bucket"] == "credible_null"
    # No arm-level route to SMD: the reported scale is shown and cannot be compared.
    mismatch = assign_bucket(trial, 0.2, "SMD")
    assert mismatch["bucket"] == "inconclusive" and mismatch["analysis_effect_type"] == "logHR"
    # The index keeps the reported analysis whatever the default scale selected.
    assert stored_analysis(trial)["analysis_effect_type"] == "logHR"
    assert reported["derived_logor_ci_high"] == pytest.approx(derived["analysis_ci_high"])
    assert reported["derived_smd_estimate"] is None


def test_derived_smds_pool_across_instruments_and_are_flagged():
    rows = [
        {**ARMS, "id": f"S{i}", "mean_intervention": 10.0 + shift, "outcome": "Pain",
         "outcome_unit": unit, "intervention": "Drug", "comparator": "Placebo"}
        for i, (shift, unit) in enumerate([(0.2, "mm VAS"), (0.0, "points"), (-0.2, "")])
    ]
    stats = analyze_studies(rows, {"sesoi": 0.2, "effectType": "SMD", "plannedN": 400})
    assert [pool["k"] for pool in stats["pools"]] == [3]
    assert stats["pools"][0]["unit"] == "SD"
    assert stats["assurance"] is not None
    assert any("arm-level summaries" in warning for warning in stats["warnings"])


def test_quoted_reported_result_outranks_the_phrase_lexicon_but_never_an_interval():
    stated_null = assign_bucket({"result_label": "positive", "reported_result": "null"})
    assert stated_null["bucket"] == "reported_null"
    assert stated_null["rationale"].startswith("The report states")
    assert assign_bucket({"result_label": "no_result_stated", "reported_result": "positive"})[
        "bucket"] == "effect"
    assert assign_bucket({"reported_result": "mixed"})["bucket"] == "inconclusive"
    # A reported interval still decides: a text "positive" cannot make this an effect.
    numeric = assign_bucket({"reported_result": "positive", "effect_type": "SMD",
                             "estimate": 0.02, "ci_low": -0.1, "ci_high": 0.14})
    assert numeric["bucket"] == "credible_null" and numeric["evidence_tier"] == "numeric"


@pytest.mark.parametrize(
    ("raw", "scale"),
    [
        ("mean difference in IOP (FC-NFC) in mmHg", "MD"),
        ("adjusted hazard ratio", "logHR"),
        ("standardized mean difference (Hedges g)", "SMD"),
        ("log odds ratio per SD", "logOR"),
        ("relative risk reduction", "logRR"),
        ("percent change", None),
    ],
)
def test_free_text_effect_scales_are_normalised_without_confusing_log_or_standardized(raw, scale):
    result = assign_bucket({"effect_type": raw, "estimate": 1.1, "ci_low": 1.0, "ci_high": 1.2})
    assert result["analysis_effect_type"] == scale


@pytest.mark.parametrize(
    ("row", "reason"),
    [
        ({"effect_type": "SMD", "estimate": 0.1, "ci_low": -0.6, "ci_high": 0.7}, "wide_interval"),
        ({"result_label": "mixed"}, "mixed_result"),
        ({"result_label": "no_result_stated"}, "no_result"),
        ({"effect_type": "HR", "estimate": 0.9, "ci_low": 0.7, "ci_high": 1.1}, "other_scale"),
        ({"is_review": True}, "review"),
    ],
)
def test_every_inconclusive_verdict_says_why_and_other_buckets_carry_no_reason(row, reason):
    verdict = assign_bucket(row)
    assert (verdict["bucket"], verdict["inconclusive_reason"]) == ("inconclusive", reason)
    assert verdict["rationale"]
    for other in ({"result_label": "null"}, {"result_label": "positive"}, {"is_retracted": True}):
        assert assign_bucket(other)["inconclusive_reason"] is None


def _trial(identifier, outcome, estimate, **extra):
    return {"id": identifier, "outcome": outcome, "effect_type": "SMD", "estimate": estimate,
            "ci_low": estimate - 0.2, "ci_high": estimate + 0.2, "intervention": f"Drug {identifier}",
            "comparator": "Placebo", **extra}


def test_model_judged_outcome_groups_pool_differently_worded_studies_and_are_flagged():
    rows = [_trial("A", "Systolic blood pressure at 12 weeks", -0.30, outcome_unit="mmHg"),
            _trial("B", "SBP, week 12", -0.20),
            _trial("C", "Office systolic BP", -0.25, outcome_unit="mm Hg"),
            _trial("D", "Quality of life", 0.40)]
    plan = {"sesoi": 0.2, "effectType": "SMD", "plannedN": 400}
    assert analyze_studies(rows, plan)["pools"] == []
    groups = {"A": "Systolic blood pressure", "B": "Systolic blood pressure",
              "C": "Systolic blood pressure"}
    stats = analyze_studies(rows, plan, None, groups)
    [pool] = stats["pools"]
    assert pool["k"] == 3 and sorted(pool["studyIds"]) == ["A", "B", "C"]
    assert (pool["outcome"], pool["unit"], pool["grouping"]) == ("Systolic blood pressure", "SD",
                                                                 "model")
    # The numbers are the ordinary random-effects pool of those three studies.
    exact = analyze_studies([{**row, "outcome": "x", "intervention": "i", "outcome_unit": ""}
                             for row in rows[:3]], plan)["pools"][0]
    assert pool["estimate"] == pytest.approx(exact["estimate"])
    assert pool["se"] == pytest.approx(exact["se"])
    assert stats["assurance"] is not None
    assert any("judged comparable by a language model" in w for w in stats["warnings"])


def test_a_model_group_never_overrides_scale_or_mean_difference_units():
    groups = dict.fromkeys("ABC", "Systolic blood pressure")
    mixed_scale = [_trial("A", "SBP", -0.3), _trial("B", "SBP", -0.2),
                   _trial("C", "SBP", 0.8, effect_type="OR", ci_low=0.6, ci_high=1.0)]
    assert analyze_studies(mixed_scale, {"effectType": "SMD"}, None, groups)["pools"] == []
    mixed_units = [_trial(x, "SBP", -3.0, effect_type="MD", outcome_unit=unit)
                   for x, unit in zip("ABC", ["mmHg", "mmHg", "kPa"])]
    assert analyze_studies(mixed_units, {"effectType": "MD", "outcomeSd": 10}, None,
                           groups)["pools"] == []
