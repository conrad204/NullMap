from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import asdict

import pandas as pd
from scipy import stats

from .meta import normalized_resolution, random_effects_meta, zone_probabilities
from .models import AnalysisConfig, EvidenceTier, Finding, Study
from .reconstruct import reconstruct_from_exact_p


QUALITATIVE_MAP = {
    "benefit": Finding.BENEFIT,
    "harm": Finding.HARM,
    "null_credible": Finding.EQUIVALENT,
    "null_underpowered": Finding.INCONCLUSIVE,
    "mixed": Finding.MIXED,
    "methodologically_failed": Finding.FAILED,
    "unclear": Finding.UNCLASSIFIABLE,
}


def _derive_se(study: Study) -> float | None:
    if study.se is not None and study.se > 0:
        return study.se
    if study.ci_low is not None and study.ci_high is not None and 0 < study.ci_level < 1:
        z = float(stats.norm.ppf(0.5 + study.ci_level / 2))
        return (study.ci_high - study.ci_low) / (2 * z)
    return None


def classify_numeric(study: Study, cfg: AnalysisConfig) -> str:
    """Human-readable secondary label; numeric estimates remain primary."""
    assert study.analysis_effect is not None and study.se is not None
    effect = study.analysis_effect
    # TOST at alpha uses a (1 - 2*alpha) CI, not the ordinary 95% CI.
    equiv_z = float(stats.norm.ppf(1 - cfg.equivalence_alpha))
    eq_low = effect - equiv_z * study.se
    eq_high = effect + equiv_z * study.se
    sig_z = float(stats.norm.ppf(1 - cfg.alpha / 2))
    sig_low = effect - sig_z * study.se
    sig_high = effect + sig_z * study.se
    if eq_low >= cfg.sesoi_low and eq_high <= cfg.sesoi_high:
        return Finding.EQUIVALENT.value
    if sig_low > cfg.sesoi_high:
        return Finding.BENEFIT.value
    if sig_high < cfg.sesoi_low:
        return Finding.HARM.value
    return Finding.INCONCLUSIVE.value


def process_study(study: Study, cfg: AnalysisConfig) -> Study:
    if study.methodologically_failed:
        study.tier, study.finding = EvidenceTier.FAILED.value, Finding.FAILED.value
        return study
    if study.completed_unreported:
        study.tier, study.finding = EvidenceTier.UNREPORTED.value, Finding.UNREPORTED.value
        return study

    se = _derive_se(study)
    if study.effect is not None and se is not None:
        study.se, study.variance = se, se * se
        study.analysis_effect = study.effect if study.higher_is_better else -study.effect
        study.tier = EvidenceTier.FULL.value
        study.finding = classify_numeric(study, cfg)
        return study

    if study.p_value is not None:
        try:
            effect, variance, measure = reconstruct_from_exact_p(study)
            study.effect, study.variance, study.se = effect, variance, math.sqrt(variance)
            study.analysis_effect = effect if study.higher_is_better else -effect
            study.measure = study.measure or measure
            study.scale_id = study.scale_id or measure
            study.reconstructed = True
            study.tier = EvidenceTier.RECONSTRUCTED.value
            study.finding = classify_numeric(study, cfg)
            study.notes.append("Effect reconstructed from an exact p-value; use in sensitivity analysis only.")
            return study
        except ValueError as exc:
            study.notes.append(f"p-value not reconstructed: {exc}")

    if study.qualitative_label or study.qualitative_text:
        study.tier = EvidenceTier.QUALITATIVE.value
        study.finding = QUALITATIVE_MAP.get(
            (study.qualitative_label or "unclear").strip().lower(), Finding.UNCLASSIFIABLE
        ).value
        return study

    study.tier, study.finding = EvidenceTier.UNEXTRACTABLE.value, Finding.UNCLASSIFIABLE.value
    return study


def _maturity(meta, cfg: AnalysisConfig) -> float:
    half_width = min(abs(cfg.sesoi_low), abs(cfg.sesoi_high))
    if half_width <= 0:
        return 0.0
    target_se = half_width * cfg.target_precision_fraction / stats.norm.ppf(1 - cfg.alpha / 2)
    target_information = 1 / (target_se * target_se)
    return min(1.0, meta.total_information / target_information)


def analyze_cluster(
    studies: list[Study],
    cfg: AnalysisConfig,
    include_reconstructed: bool = False,
    exclude_high_risk: bool = False,
) -> dict:
    counts = Counter(s.tier for s in studies)
    eligible = [
        s for s in studies
        if s.pico_compatible and s.primary_result and s.analysis_effect is not None and s.variance is not None
        and (not exclude_high_risk or s.risk_of_bias != "high")
        and (include_reconstructed or not s.reconstructed)
    ]
    result = {
        "cluster_id": studies[0].cluster_id,
        "n_records": len(studies),
        "tier_counts": dict(counts),
        "qualitative_finding_counts": dict(Counter(s.finding for s in studies if s.tier == EvidenceTier.QUALITATIVE.value)),
        "n_completed_unreported": counts[EvidenceTier.UNREPORTED.value],
        "n_methodologically_failed": counts[EvidenceTier.FAILED.value],
        "n_high_risk_excluded": sum(s.risk_of_bias == "high" for s in studies),
        "n_numeric_eligible": len(eligible),
    }
    if len(eligible) < cfg.min_studies_for_pooling:
        result.update({
            "status": "insufficient_numeric_evidence",
            "redundancy_score": None,
            "reason": "Too few compatible, primary, non-high-risk numeric estimates.",
        })
        return result

    signatures = {(s.measure, s.scale_id, s.outcome, s.timepoint, s.higher_is_better) for s in eligible}
    if len(signatures) != 1:
        result.update({
            "status": "not_poolable",
            "redundancy_score": None,
            "reason": "Eligible estimates do not share measure, scale, outcome, timepoint, and orientation.",
        })
        return result

    meta = random_effects_meta([s.analysis_effect for s in eligible], [s.variance for s in eligible], cfg.alpha)
    predictive_sd = math.sqrt(meta.tau2 + meta.se**2)
    probabilities = zone_probabilities(meta.mean, predictive_sd, cfg.sesoi_low, cfg.sesoi_high)
    resolution = normalized_resolution(probabilities)
    maturity = _maturity(meta, cfg)
    score = 100 * resolution * maturity
    dominant_zone = max(probabilities, key=probabilities.get)
    prediction_crosses_both = (
        meta.prediction_low is not None
        and meta.prediction_low < cfg.sesoi_low
        and meta.prediction_high > cfg.sesoi_high
    )
    # A very small-k prediction interval can be wide solely because its t critical
    # value is huge. Call evidence conflicting only when observed heterogeneity is
    # also substantial; otherwise it is sparse/predictively uncertain.
    conflict = meta.i2 >= cfg.high_i2_flag and prediction_crosses_both
    if conflict:
        status = "conflicting_evidence"
    elif maturity < 0.5:
        status = "sparse_or_imprecise"
    elif score >= cfg.resolved_score_threshold:
        status = f"resolved_{dominant_zone}"
    else:
        status = "uncertain"

    result.update({
        "status": status,
        "redundancy_score": round(score, 1),
        "score_definition": "100 × predictive-zone resolution × evidence maturity",
        "dominant_zone": dominant_zone,
        "predictive_zone_probabilities": {k: round(v, 4) for k, v in probabilities.items()},
        "resolution_component": round(resolution, 4),
        "maturity_component": round(maturity, 4),
        "conflict_flag": conflict,
        "meta_analysis": {k: (round(v, 6) if isinstance(v, float) and math.isfinite(v) else v)
                          for k, v in meta.to_dict().items()},
    })
    return result


def run_pipeline(studies: list[Study], cfg: AnalysisConfig | None = None) -> dict:
    cfg = cfg or AnalysisConfig()
    cfg.validate()
    processed = [process_study(s, cfg) for s in studies]
    grouped: dict[str, list[Study]] = defaultdict(list)
    for study in processed:
        grouped[study.cluster_id].append(study)

    primary = [analyze_cluster(group, cfg, include_reconstructed=False, exclude_high_risk=False)
               for group in grouped.values()]
    bias_sensitivity = [analyze_cluster(group, cfg, include_reconstructed=False, exclude_high_risk=True)
                        for group in grouped.values()]
    p_sensitivity = [analyze_cluster(group, cfg, include_reconstructed=True, exclude_high_risk=True)
                     for group in grouped.values()]
    return {
        "config": asdict(cfg),
        "clusters_primary": primary,
        "clusters_excluding_high_risk": bias_sensitivity,
        "clusters_with_p_reconstruction": p_sensitivity,
        "studies": [s.to_dict() for s in processed],
        "guardrails": [
            "PICO embeddings may screen candidates but do not authorize pooling.",
            "Citation counts are discovery metadata, never statistical weights.",
            "Exact p-value reconstruction requires test metadata and is sensitivity-only.",
            "Censored p-values are retained as text constraints, not converted to point estimates.",
            "Risk-of-bias weighting is not used in the primary analysis; high-risk studies are excluded in sensitivity logic.",
            "A redundancy score is not a recommendation to stop research; impact, feasibility, external validity, and safety remain separate.",
        ],
    }


def studies_from_csv(path: str) -> list[Study]:
    frame = pd.read_csv(path)
    valid = set(Study.__dataclass_fields__)
    rows = []
    for row in frame.to_dict(orient="records"):
        clean = {k: v for k, v in row.items() if k in valid and not pd.isna(v)}
        rows.append(Study(**clean))
    return rows
