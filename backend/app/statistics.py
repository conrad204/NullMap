"""Conservative, deterministic evidence classification and trial-planning statistics.

Request SESOI units are SD units for SMD, original outcome units for MD, and
*natural-log* units for logOR/logRR/logHR. Raw OR/RR/HR request scales also work,
but their SESOI must be a multiplicative margin > 1 (e.g. OR=1.25 maps to
log(1.25), with equivalence bounds [1/1.25, 1.25]). Study ratio estimates and
interval endpoints are always log-transformed before analysis.

References:
- https://www.cochrane.org/authors/handbooks-and-manuals/handbook/current/chapter-06
  (log scales, CI-to-SE, and estimate-plus-exact-Wald-p reconstruction).
- https://www.statsmodels.org/stable/generated/statsmodels.stats.meta_analysis.combine_effects.html
  (DerSimonian-Laird, method_re="chi2"). We explicitly clamp negative tau².
- https://doi.org/10.1002/pst.175 (assurance as expected power).
- Cochrane Handbook 6.4-6.5 and Hedges (1981): effect sizes from reported arm-level
  means/SDs or event counts, with the small-sample correction for standardized differences.
- Egger et al. (1997), BMJ 315:629 (regression test for funnel-plot asymmetry), applied
  only to pools of at least ten studies as Cochrane Handbook 13.3.5.3 recommends.

Assurance here is two-sided statistical significance, including effects in the
unfavorable direction. It is not a probability of clinically meaningful benefit.
All intervals and power calculations use a normal approximation.
"""

from __future__ import annotations

import calendar
import math
import re
import warnings as python_warnings
from collections import defaultdict
from datetime import UTC, date, datetime
from statistics import NormalDist
from typing import Any

import numpy as np
from scipy.stats import beta as beta_dist
from scipy.stats import t as student_t
from statsmodels.stats.meta_analysis import combine_effects

_NORMAL = NormalDist()
_Z95 = _NORMAL.inv_cdf(0.975)
_MDE_FACTOR = _Z95 + _NORMAL.inv_cdf(0.8)
_RATIO_TYPES = {"OR": "logOR", "RR": "logRR", "HR": "logHR"}
_MAX_REQUIRED_N = 10_000_000
# Scales computable from arm-level summaries, in the order used when none was requested.
DERIVED_SCALES = ("SMD", "logOR", "MD", "logRR")
# Why a study is inconclusive. Only ``wide_interval`` describes the study itself (it
# could not tell no effect from a meaningful one); the others describe what we could read.
INCONCLUSIVE_REASONS = (
    "wide_interval", "mixed_result", "no_result", "other_scale", "no_threshold", "review"
)
_MAX_PLAUSIBLE_SMD = 5.0
# Funnel-plot asymmetry tests have little power below ten studies (Cochrane 13.3.5.3).
EGGER_MIN_STUDIES = 10
# Conventional screening level for asymmetry tests, which are underpowered at 0.05.
EGGER_ALPHA = 0.10
# Pseudo-observations one verdict adds to the Beta posterior, by how its numbers were read.
# A quoted CI is one observation; a text-only claim is half of one.
PURSUIT_TIER_WEIGHTS = {"numeric": 1.0, "derived": 1.0, "reconstructed": 0.75, "text_only": 0.5}
# Share of the informative weight on the minority side above which the record is contested.
PURSUIT_CONFLICT_SHARE = 0.3


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _text(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _effect_type(value: Any) -> str | None:
    cleaned = re.sub(r"[\s_()-]", "", str(value or "")).upper()
    aliases = {
        "SMD": "SMD",
        "STANDARDIZEDMEANDIFFERENCE": "SMD",
        "STANDARDISEDMEANDIFFERENCE": "SMD",
        "COHENSD": "SMD",
        "HEDGESG": "SMD",
        "MD": "MD",
        "MEANDIFFERENCE": "MD",
        "DIFFERENCEINMEANS": "MD",
        "OR": "OR",
        "ODDSRATIO": "OR",
        "RR": "RR",
        "RISKRATIO": "RR",
        "RELATIVERISK": "RR",
        "HR": "HR",
        "HAZARDRATIO": "HR",
        "LOGOR": "logOR",
        "LOGODDSRATIO": "logOR",
        "LNOR": "logOR",
        "LOGRR": "logRR",
        "LOGRISKRATIO": "logRR",
        "LNRR": "logRR",
        "LOGHR": "logHR",
        "LOGHAZARDRATIO": "logHR",
        "LNHR": "logHR",
    }
    if cleaned in aliases:
        return aliases[cleaned]
    # Extracted scale names are free text ("mean difference in IOP (mmHg)"). Log scales
    # and the standardized difference are tested first so they are not read as plain ones.
    for name in ("LOGODDSRATIO", "LOGRISKRATIO", "LOGHAZARDRATIO", "STANDARDIZEDMEANDIFFERENCE",
                 "STANDARDISEDMEANDIFFERENCE", "HAZARDRATIO", "ODDSRATIO", "RISKRATIO",
                 "RELATIVERISK", "MEANDIFFERENCE", "DIFFERENCEINMEANS"):
        if name in cleaned:
            return aliases[name]
    return None


def _analysis_type(value: Any) -> str | None:
    kind = _effect_type(value)
    return _RATIO_TYPES.get(kind, kind)


def _margin(sesoi: Any, effect_type: str) -> float | None:
    margin = _number(sesoi)
    if margin is None or margin <= 0 or _effect_type(effect_type) is None:
        return None
    if _effect_type(effect_type) in _RATIO_TYPES:
        return math.log(margin) if margin > 1 else None
    return margin


def _completion_date(value: Any) -> date | None:
    """Use the latest possible day for partial dates, avoiding early overdue labels."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        text = str(value or "").strip()
        if re.fullmatch(r"\d{4}", text):
            return date(int(text), 12, 31)
        if re.fullmatch(r"\d{4}-\d{2}", text):
            year, month = map(int, text.split("-"))
            return date(year, month, calendar.monthrange(year, month)[1])
        return date.fromisoformat(text[:10])
    except (ValueError, TypeError):
        return None


def _more_than_twelve_months_ago(value: Any, today: date) -> bool:
    completed = _completion_date(value)
    if completed is None or completed.year >= 9999:
        return False
    anniversary = completed.replace(
        year=completed.year + 1,
        day=min(completed.day, calendar.monthrange(completed.year + 1, completed.month)[1]),
    )
    return today > anniversary


def _is_registry(study: dict) -> bool:
    return study.get("source") in {"ctgov", "merged"} or bool(study.get("nct_ids"))


def _unreported(study: dict, today: date) -> bool:
    # Unknown reporting status cannot be treated as confirmed absence of results.
    return (
        _is_registry(study)
        and str(study.get("overall_status", "")).upper() == "COMPLETED"
        and study.get("has_results") is False
        and study.get("has_linked_publication") is False
        and not study.get("result_pmids")
        and study.get("source") not in {"merged", "openalex"}
        and _more_than_twelve_months_ago(study.get("primary_completion_date"), today)
    )


def derived_field(scale: str, name: str) -> str:
    return f"derived_{scale.lower()}_{name}"


def _count(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None and number >= 0 and number.is_integer() else None


def derive_effects(study: dict) -> dict[str, dict]:
    """Effect sizes computed from arm-level summaries, as intervention minus comparator.

    Nothing is imputed: a continuous effect needs both means, both SDs and both arm
    sizes; a binary effect needs both event counts and both arm sizes. Hazard ratios
    cannot be recovered from summaries and are never derived.
    """
    effects: dict[str, dict] = {}
    n1, n2 = _count(study.get("n_intervention")), _count(study.get("n_comparator"))
    if n1 is None or n2 is None or n1 < 2 or n2 < 2:
        return effects
    m1, m2 = _number(study.get("mean_intervention")), _number(study.get("mean_comparator"))
    sd1, sd2 = _number(study.get("sd_intervention")), _number(study.get("sd_comparator"))
    if None not in (m1, m2, sd1, sd2) and sd1 > 0 and sd2 > 0:
        difference = m1 - m2
        pooled_sd = math.sqrt(((n1 - 1) * sd1 * sd1 + (n2 - 1) * sd2 * sd2) / (n1 + n2 - 2))
        d = difference / pooled_sd
        # Arms several pooled SDs apart almost always mean a standard error was reported
        # as an SD, so neither the SMD nor the SE of the mean difference is usable.
        if abs(d) <= _MAX_PLAUSIBLE_SMD:
            correction = 1 - 3 / (4 * (n1 + n2 - 2) - 1)
            effects["MD"] = {
                "estimate": difference,
                "se": math.sqrt(sd1 * sd1 / n1 + sd2 * sd2 / n2),
                "note": "Mean difference and SE computed from reported arm means, SDs and sizes.",
            }
            effects["SMD"] = {
                "estimate": correction * d,
                "se": correction * math.sqrt((n1 + n2) / (n1 * n2) + d * d / (2 * (n1 + n2))),
                "note": "Hedges' g computed from reported arm means, SDs and sizes (pooled SD, "
                "small-sample correction).",
            }
    e1, e2 = _count(study.get("events_intervention")), _count(study.get("events_comparator"))
    if e1 is not None and e2 is not None and e1 <= n1 and e2 <= n2:
        cells = [float(e1), float(n1 - e1), float(e2), float(n2 - e2)]
        # With no events, or only events, in both arms the ratio carries no information.
        if not (e1 == e2 == 0 or (e1 == n1 and e2 == n2)):
            note = "computed from reported arm event counts and sizes"
            if 0 in cells:
                cells = [cell + 0.5 for cell in cells]
                note += " with a 0.5 continuity correction for an empty cell"
            a, b, c, d_ = cells
            effects["logOR"] = {
                "estimate": math.log(a * d_ / (b * c)),
                "se": math.sqrt(1 / a + 1 / b + 1 / c + 1 / d_),
                "note": f"Log odds ratio {note}.",
            }
            variance = 1 / a - 1 / (a + b) + 1 / c - 1 / (c + d_)
            if variance > 0:
                effects["logRR"] = {
                    "estimate": math.log((a / (a + b)) / (c / (c + d_))),
                    "se": math.sqrt(variance),
                    "note": f"Log risk ratio {note}.",
                }
    return {
        scale: effect
        for scale, effect in effects.items()
        if math.isfinite(effect["estimate"]) and math.isfinite(effect["se"]) and effect["se"] > 0
    }


def _numeric_evidence(study: dict, requested: str | None = None) -> dict:
    """Reported numbers first; arm-level derivation only fills a gap on the requested scale.

    A reported interval on the requested scale is never replaced, so registry
    primary-outcome analyses stay authoritative.
    """
    result = _reported_evidence(study)
    derived = derive_effects(study)
    for scale in DERIVED_SCALES:
        effect = derived.get(scale)
        for name in ("estimate", "ci_low", "ci_high"):
            result[derived_field(scale, name)] = None
        if effect:
            result[derived_field(scale, "estimate")] = effect["estimate"]
            result[derived_field(scale, "ci_low")] = effect["estimate"] - _Z95 * effect["se"]
            result[derived_field(scale, "ci_high")] = effect["estimate"] + _Z95 * effect["se"]
    target = _analysis_type(requested)
    reported = result["analysis_ci"] is not None
    if reported and (target is None or result["analysis_effect_type"] == target):
        return result
    scale = target if target in derived else None
    if scale is None and not reported:
        scale = next((name for name in DERIVED_SCALES if name in derived), None)
    if scale is None:
        return result
    effect = derived[scale]
    low, high = effect["estimate"] - _Z95 * effect["se"], effect["estimate"] + _Z95 * effect["se"]
    result.update(
        analysis_effect_type=scale,
        analysis_estimate=effect["estimate"],
        analysis_se=effect["se"],
        analysis_ci=[low, high],
        analysis_ci_low=low,
        analysis_ci_high=high,
        evidence_tier="derived",
        mde=_MDE_FACTOR * effect["se"],
        numeric_notes=[*result["numeric_notes"], effect["note"]],
    )
    return result


def stored_analysis(study: dict) -> dict:
    """Scale-neutral analysis fields for the index: reported numbers, else the first derived.

    Buckets are written on the default scale, but a query may use any scale. Storing the
    reported analysis (rather than whatever the default scale selected) lets the
    aggregation script find it, with ``derived_*`` fields covering every other scale.
    """
    evidence = _numeric_evidence(study)
    keys = ("analysis_effect_type", "analysis_estimate", "analysis_se", "analysis_ci",
            "analysis_ci_low", "analysis_ci_high", "evidence_tier", "mde", "numeric_notes")
    return {key: evidence[key] for key in keys}


def _reported_evidence(study: dict) -> dict:
    kind = _effect_type(study.get("effect_type"))
    result = {
        "analysis_effect_type": _analysis_type(kind),
        "analysis_estimate": None,
        "analysis_se": None,
        "analysis_ci": None,
        "analysis_ci_low": None,
        "analysis_ci_high": None,
        "evidence_tier": "text_only",
        "mde": None,
        "numeric_notes": [],
    }
    if kind is None:
        if any(study.get(key) is not None for key in ("estimate", "ci_low", "ci_high")):
            result["numeric_notes"].append("Effect scale is missing or unsupported.")
        return result

    estimate = _number(study.get("estimate"))
    low, high = _number(study.get("ci_low")), _number(study.get("ci_high"))
    if kind in _RATIO_TYPES:
        if any(value is not None and value <= 0 for value in (estimate, low, high)):
            result["numeric_notes"].append(
                "Ratio estimates and confidence bounds must be positive."
            )
            return result
        estimate, low, high = (
            math.log(value) if value is not None else None for value in (estimate, low, high)
        )
        result["numeric_notes"].append("Ratio estimate and CI transformed to natural-log units.")
    result["analysis_estimate"] = estimate
    se = None
    tier = "numeric"

    sides = str(study.get("ci_sides", "TWO_SIDED")).upper().replace("-", "_")
    if sides in {"ONE_SIDED", "ONESIDED", "1"}:
        result["numeric_notes"].append(
            "One-sided inference cannot establish a two-sided 95% interval."
        )
        return result

    if low is not None and high is not None:
        if low >= high or (estimate is not None and not low <= estimate <= high):
            result["numeric_notes"].append(
                "Invalid or inconsistent confidence interval was excluded."
            )
            return result
        level = _number(study.get("ci_level", study.get("confidence_level", 0.95)))
        if level is not None and level > 1:
            level /= 100
        if level is None or not 0 < level < 1:
            result["numeric_notes"].append("Invalid confidence level; interval was excluded.")
            return result
        z = _NORMAL.inv_cdf((1 + level) / 2)
        if z <= 0 or not math.isfinite(z):
            result["numeric_notes"].append(
                "Confidence level is outside usable numerical precision."
            )
            return result
        se = (high - low) / (2 * z)
        if not math.isclose(level, 0.95):
            if estimate is None:
                result["numeric_notes"].append(
                    "A non-95% CI without an estimate cannot be converted."
                )
                return result
            low, high = estimate - _Z95 * se, estimate + _Z95 * se
            tier = "reconstructed"
            result["numeric_notes"].append("95% CI reconstructed under a normal approximation.")
    elif estimate is not None:
        # A reported SE is usable on a difference/log scale. A raw-ratio SE is
        # not a log-scale SE and is deliberately not silently reinterpreted.
        reported_se = _number(study.get("se", study.get("standard_error")))
        if reported_se is not None and reported_se > 0 and kind not in _RATIO_TYPES:
            se = reported_se
            result["numeric_notes"].append(
                "95% normal CI calculated from reported estimate and SE."
            )
        else:
            p = _number(study.get("p_value"))
            modifier = _text(study.get("p_value_operator", study.get("p_value_modifier")))
            exact = study.get("p_value_is_exact") is not False and modifier in {"", "="}
            if p is not None and 0 < p < 1 and p / 2 > 0 and estimate != 0 and exact:
                # inv_cdf(p/2), unlike inv_cdf(1-p/2), remains finite for tiny p.
                z = -_NORMAL.inv_cdf(p / 2)
                se = abs(estimate) / z
                tier = "reconstructed"
                result["numeric_notes"].append(
                    "SE reconstructed from estimate and exact p assuming a two-sided normal Wald test."
                )
        if se is not None:
            low, high = estimate - _Z95 * se, estimate + _Z95 * se

    if se is None or not math.isfinite(se) or se <= 0 or not 0 < se * se < math.inf:
        if study.get("p_value") is not None:
            result["numeric_notes"].append(
                "P-value and sample size alone do not identify an effect or standard error."
            )
        return result
    if low is None or high is None or not all(math.isfinite(x) for x in (low, high)):
        return result
    result.update(
        analysis_se=se,
        analysis_ci=[low, high],
        analysis_ci_low=low,
        analysis_ci_high=high,
        evidence_tier=tier,
        mde=_MDE_FACTOR * se,
    )
    return result


def assign_bucket(
    study: dict,
    sesoi: float = 0.2,
    effect_type: str = "SMD",
    today: date | None = None,
) -> dict:
    """Return classification metadata without changing the input study document.

    Text-only null claims never establish equivalence: they are ``reported_null``,
    never ``credible_null``. A text-only positive label is likewise a provisional
    reported effect, with magnitude explicitly unverified.
    Methodological failures override numerical findings. Reviews are discovery
    sources, never independent patient samples in a pool.
    """
    result = _numeric_evidence(study, effect_type)
    result.update(
        bucket="inconclusive", rationale="", significant_but_trivial=False,
        inconclusive_reason=None,
    )
    status = str(study.get("overall_status", "")).upper()
    failures = []
    if study.get("is_retracted") is True:
        failures.append("Publication is retracted")
    if status in {"TERMINATED", "WITHDRAWN", "SUSPENDED"}:
        reason = str(study.get("why_stopped") or "reason not provided")
        failures.append(f"Registry status {status.lower()}: {reason}")
    actual, planned = (
        _number(study.get("enrollment_actual")),
        _number(study.get("enrollment_planned")),
    )
    if actual is not None and planned is not None and 0 <= actual < planned / 2:
        failures.append("Actual enrollment was less than half of planned enrollment")
    if study.get("has_control") is False and study.get("is_review") is not True:
        failures.append("No control arm was identified")
    if failures:
        result.update(bucket="failed", rationale="; ".join(failures) + ".")
        return result
    if study.get("is_review") is True:
        result["rationale"] = "Review used for discovery; not an independent primary study."
        result["inconclusive_reason"] = "review"
        return result
    if _unreported(study, today or datetime.now(UTC).date()):
        result.update(
            bucket="unreported",
            rationale="Registered trial completed more than 12 calendar months ago, with no posted "
            "results or linked result publication in the available metadata.",
        )
        return result

    interval = result["analysis_ci"]
    if interval is None:
        # A quoted statement read from the report outranks the index-time phrase lexicon,
        # but a between-group result needs a comparison group: once a paper has been read
        # and no control arm was quoted (a case report, a single-arm series), neither its
        # stated result nor its wording earns a verdict.
        comparative = study.get("has_control") is True or _is_registry(study)
        read = _text(study.get("extraction_status")) == "verified"
        stated = _text(study.get("reported_result")) if comparative else ""
        label = stated or ("" if read and not comparative else _text(study.get("result_label")))
        origin = "The report states" if stated else "Abstract classifier reports"
        if label == "positive":
            result.update(
                bucket="effect",
                rationale=f"{origin} an effect; statistical significance and "
                "meaningful magnitude are unverified without compatible numerical evidence.",
            )
        elif label == "null":
            result.update(
                bucket="reported_null",
                rationale=f"{origin} no significant difference. Without a compatible "
                "numerical CI, this cannot establish absence of a meaningful effect.",
            )
        elif label == "mixed":
            result["inconclusive_reason"] = "mixed_result"
            result["rationale"] = (
                f"{origin} conflicting primary results, and no compatible numerical CI "
                "is available to weigh them."
            )
        else:
            result["inconclusive_reason"] = "no_result"
            result["rationale"] = (
                "No comparative result could be read: no usable numbers and no stated outcome."
            )
        return result

    margin = _margin(sesoi, effect_type)
    if margin is None:
        result["inconclusive_reason"] = "no_threshold"
        result["rationale"] = (
            "A positive SESOI on a supported effect scale is required; raw ratio margins must exceed 1."
        )
        return result
    if result["analysis_effect_type"] != _analysis_type(effect_type):
        result["inconclusive_reason"] = "other_scale"
        result["rationale"] = (
            f"Study uses {result['analysis_effect_type']} but the SESOI uses "
            f"{_analysis_type(effect_type) or effect_type}; meaningfulness cannot be compared across scales."
        )
        return result

    low, high = interval
    excludes_zero = low > 0 or high < 0
    estimate = result["analysis_estimate"]
    if low > -margin and high < margin:
        result.update(
            bucket="credible_null",
            significant_but_trivial=excludes_zero,
            rationale="The entire 95% CI lies strictly inside the SESOI equivalence bounds."
            + (
                " The effect is statistically significant but smaller than the meaningful threshold."
                if excludes_zero
                else ""
            ),
        )
    elif excludes_zero and estimate is not None and abs(estimate) >= margin:
        result.update(
            bucket="effect",
            rationale="95% CI excludes the null and the point estimate reaches the SESOI; "
            "the CI need not exclude effects smaller than the SESOI.",
        )
    else:
        result["inconclusive_reason"] = "wide_interval"
        result["rationale"] = (
            "The 95% CI does not establish equivalence and the evidence does not establish "
            "a statistically significant effect whose estimate reaches the SESOI."
        )
    return result


def _unit(study: dict, kind: str) -> str:
    unit = _text(study.get("outcome_unit"))
    if kind == "SMD" and unit in {"", "sd", "sds", "standard deviation", "standard deviations"}:
        return "SD"
    return unit or (
        {"logOR": "log odds ratio", "logRR": "log risk ratio", "logHR": "log hazard ratio"}.get(
            kind, ""
        )
    )


def _study_id(study: dict, position: int) -> str:
    return str(study.get("id") or study.get("study_id") or f"study-{position + 1}")


def _egger(effects: np.ndarray, ses: np.ndarray) -> dict | None:
    """Egger's regression test for funnel-plot asymmetry, or None when it cannot be run.

    The standard normal deviate ``estimate / se`` is regressed on precision ``1 / se``
    by ordinary least squares; the intercept is the asymmetry, zero under symmetry.
    Asymmetry has many causes besides publication bias (heterogeneity, small-study
    effects, chance), so the result is reported, never used to adjust an estimate.
    """
    k = len(effects)
    if k < EGGER_MIN_STUDIES:
        return None
    precision = 1.0 / ses
    deviate = effects / ses
    center = float(precision.mean())
    spread = float(np.sum((precision - center) ** 2))
    # Studies of near-identical precision give a funnel with no vertical extent.
    if not math.isfinite(spread) or spread <= 0:
        return None
    slope = float(np.sum((precision - center) * (deviate - deviate.mean())) / spread)
    intercept = float(deviate.mean() - slope * center)
    degrees = k - 2
    residual_variance = float(np.sum((deviate - intercept - slope * precision) ** 2)) / degrees
    se = math.sqrt(residual_variance * (1 / k + center * center / spread))
    if not math.isfinite(intercept) or not math.isfinite(se) or se <= 0:
        return None
    statistic = intercept / se
    critical = float(student_t.ppf(0.975, degrees))
    p_value = float(2 * student_t.sf(abs(statistic), degrees))
    return {
        "intercept": intercept,
        "se": se,
        "ci": [intercept - critical * se, intercept + critical * se],
        "t": statistic,
        "df": degrees,
        "pValue": p_value,
        "slope": slope,
        "asymmetric": p_value < EGGER_ALPHA,
        "alpha": EGGER_ALPHA,
        "method": "Egger regression of the standard normal deviate on precision (OLS)",
    }


def _pursuit(
    classifications: list[dict], pools: list[dict], margin: float | None, kind: str | None
) -> dict:
    """Posterior probability that a real effect exists, from a Beta(1, 1) prior.

    Every verdict that answers the yes/no question moves the posterior: ``effect`` is a
    success, ``credible_null`` and ``reported_null`` are failures, weighted by how the
    numbers were read (``PURSUIT_TIER_WEIGHTS``). Inconclusive, failed and unreported
    records carry no information about the answer and leave the prior alone. Studies
    are treated as exchangeable, so contradicting results narrow the interval around
    one half rather than widening it; the ``conflict`` share and the ``contested`` state
    report that explicitly instead of hiding it in a confident-looking 50%.

    With a numeric pool, ``pMeaningful`` is the probability under the predictive
    Normal(pooled mean, tau² + SE²) that the true effect reaches the SESOI in either
    direction, and ``pFavours`` the probability that it is positive as coded.
    """
    successes = failures = 0.0
    counted = {"effect": 0, "credible_null": 0, "reported_null": 0}
    uninformative = 0
    for verdict in classifications:
        weight = PURSUIT_TIER_WEIGHTS.get(verdict["evidence_tier"], 0.5)
        bucket = verdict["bucket"]
        if bucket == "effect":
            successes += weight
        elif bucket in {"credible_null", "reported_null"}:
            failures += weight
        else:
            uninformative += 1
            continue
        counted[bucket] += 1
    alpha, beta = 1.0 + successes, 1.0 + failures
    informative = successes + failures
    low, high = (float(value) for value in beta_dist.ppf([0.025, 0.975], alpha, beta))
    conflict = min(successes, failures) / informative if informative else 0.0
    if informative == 0:
        state = "unknown"
    elif conflict >= PURSUIT_CONFLICT_SHARE and informative >= 3:
        state = "contested"
    elif low <= 0.5 <= high:
        state = "open"
    else:
        state = "favours_effect" if low > 0.5 else "favours_null"
    # Chance the record's current lean is wrong: twice the smaller tail around even odds.
    # 1 with no evidence or a perfect split, near 0 once one side dominates.
    below = float(beta_dist.cdf(0.5, alpha, beta))
    result = {
        "prior": [1.0, 1.0],
        "posterior": [alpha, beta],
        "pEffect": alpha / (alpha + beta),
        "pOpen": 2 * min(below, 1 - below),
        "ci": [low, high],
        "successes": successes,
        "failures": failures,
        "counted": counted,
        "uninformative": uninformative,
        "conflict": conflict,
        "state": state,
        "pMeaningful": None,
        "pFavours": None,
        "poolStudyIds": [],
        "method": (
            "Beta(1, 1) prior updated with tier-weighted verdicts; 95% equal-tailed credible "
            "interval. Pool probabilities integrate Normal(pooled mean, tau² + SE²)."
        ),
    }
    matching = [pool for pool in pools if pool["effectType"] == kind]
    if margin is not None and len(matching) == 1:
        pool = matching[0]
        spread = math.sqrt(pool["tau2"] + pool["se"] ** 2)
        if spread > 0:
            result.update(
                pMeaningful=_NORMAL.cdf((-margin - pool["estimate"]) / spread)
                + (1 - _NORMAL.cdf((margin - pool["estimate"]) / spread)),
                pFavours=1 - _NORMAL.cdf(-pool["estimate"] / spread),
                poolStudyIds=pool["studyIds"],
            )
    return result


# Below this chance a new study of the same design is not recommended.
PURSUE_DEPRIORITIZE = 0.25
# From here the record is open enough to repeat the design as asked.
PURSUE_AS_PLANNED = 0.5


def _decide(
    pursuit: dict, planned_se: float | None, margin: float | None, alpha: float | None, drawer: dict
) -> None:
    """Turn the posterior into the chance a new study should be run, and a recommendation.

    ``pPursue = pOpen x power``: the chance the record's lean is wrong times the chance the
    planned design would detect the SESOI if it is real (two-sided test at ``alpha`` with
    the planned SE). Without a computable design the power factor is left out and said
    so. A contested record can score high, but the recommendation is then to change the
    design, since repeating it adds to the disagreement rather than resolving it.
    """
    power = None
    if planned_se is not None and margin is not None and alpha is not None and planned_se > 0:
        z = -_NORMAL.inv_cdf(alpha / 2)
        power = (1 - _NORMAL.cdf(z - margin / planned_se)) + _NORMAL.cdf(-z - margin / planned_se)
    p_pursue = pursuit["pOpen"] * (power if power is not None else 1.0)
    state = pursuit["state"]
    if state == "contested":
        recommendation = "pursue_with_changes"
    elif p_pursue >= PURSUE_AS_PLANNED:
        recommendation = "pursue"
    elif p_pursue >= PURSUE_DEPRIORITIZE:
        recommendation = "pursue_with_changes"
    else:
        recommendation = "deprioritize"
    reasons = []
    if state == "unknown":
        reasons.append(
            "No read study answered the question either way; a new study would be the first answer."
        )
    elif state == "contested":
        reasons.append(
            f"Studies disagree ({pursuit['conflict']:.0%} of the evidence weight on the minority "
            "side); a study that separates the conditions under which the effect appears is worth "
            "more than another undifferentiated trial."
        )
    elif state == "open":
        reasons.append(
            f"The record leans one way ({pursuit['pEffect']:.0%} chance of a real effect) but the "
            "credible interval still spans even odds."
        )
    else:
        side = "a real effect" if state == "favours_effect" else "no effect"
        reasons.append(
            f"The record already favors {side}: only a {pursuit['pOpen']:.0%} chance that lean "
            "is wrong."
        )
    if power is not None:
        reasons.append(
            f"The planned design has {power:.0%} power to detect the SESOI, so it would answer the "
            "question if run."
            if power >= 0.8
            else f"The planned design has only {power:.0%} power to detect the SESOI; it would likely "
            "end inconclusive, which lowers the chance it is worth running as planned."
        )
    else:
        reasons.append(
            "No planned sample size or SESOI margin was usable, so power is not factored in."
        )
    if drawer.get("share") and drawer["share"] >= 0.3:
        reasons.append(
            f"{drawer['share']:.0%} of eligible completed trials never reported; the record may "
            "understate null results, so the lean above may be too optimistic."
        )
    pursuit.update(power=power, pPursue=p_pursue, recommendation=recommendation, reasons=reasons)


def _pool(group: list[tuple[dict, dict, str]], key: tuple) -> dict:
    effects = np.array([row[1]["analysis_estimate"] for row in group], dtype=float)
    ses = np.array([row[1]["analysis_se"] for row in group], dtype=float)
    variances = np.square(ses)
    # statsmodels DL can emit invalid interim random-effect weights for negative
    # tau²; use its estimator, then recompute all weights with tau² clamped at 0.
    with python_warnings.catch_warnings(), np.errstate(all="ignore"):
        python_warnings.simplefilter("ignore", RuntimeWarning)
        combined = combine_effects(effects, variances, method_re="chi2")
    tau2 = max(0.0, float(combined.tau2))
    weights = 1 / (variances + tau2)
    estimate = float(np.sum(weights * effects) / np.sum(weights))
    se = float(math.sqrt(1 / np.sum(weights)))
    kind, outcome, unit, intervention, comparator, direction = key
    return {
        "effectType": kind,
        "outcome": group[0][0].get("outcome") or outcome,
        "unit": unit,
        "k": len(group),
        "estimate": estimate,
        "se": se,
        "ci": [estimate - _Z95 * se, estimate + _Z95 * se],
        "tau2": tau2,
        "studyIds": [row[2] for row in group],
        "intervention": intervention,
        "comparator": comparator,
        "direction": direction or "as reported",
        "method": "DerSimonian-Laird (nonnegative tau²), normal 95% CI",
        "egger": _egger(effects, ses),
    }


def _variance_factor(kind: str | None, plan: dict, warnings: list[str]) -> float | None:
    if kind == "SMD":
        return 4.0
    if kind == "MD":
        sd = _number(plan.get("outcomeSd"))
        if sd is not None and sd > 0:
            return 4 * sd * sd
        warnings.append(
            "Mean-difference planning needs a positive outcomeSd in the outcome's units."
        )
    elif kind in {"logOR", "logRR"}:
        risk = _number(plan.get("baselineRisk"))
        if risk is not None and 0 < risk < 1:
            return 4 / (risk * (1 - risk)) if kind == "logOR" else 4 * (1 - risk) / risk
        warnings.append("Binary ratio planning needs baselineRisk strictly between 0 and 1.")
    elif kind == "logHR":
        warnings.append(
            "Hazard-ratio planning needs expected events and follow-up; total N is insufficient."
        )
    else:
        warnings.append("Planning is unavailable for this effect scale.")
    return None


def _assurance(estimate: float, prior_variance: float, se: float, alpha: float) -> float:
    """Exact integral of normal-approximation two-sided power over a normal prior."""
    threshold = -_NORMAL.inv_cdf(alpha / 2) * se
    predictive_sd = math.sqrt(prior_variance + se * se)
    probability = _NORMAL.cdf((-threshold - estimate) / predictive_sd)
    probability += _NORMAL.cdf((estimate - threshold) / predictive_sd)
    return min(1.0, max(0.0, probability))


def _required_n(pool: dict, factor: float, alpha: float, target: float = 0.8) -> int | None:
    prior_variance = pool["tau2"] + pool["se"] ** 2

    def enough(n: int) -> bool:
        return _assurance(pool["estimate"], prior_variance, math.sqrt(factor / n), alpha) >= target

    high = 4
    while high < _MAX_REQUIRED_N and not enough(high):
        high = min(2 * high, _MAX_REQUIRED_N)
    if not enough(high):
        return None
    # Search numbers of participants per arm, so the returned total N is even.
    low_arm, high_arm = 2, high // 2
    while low_arm < high_arm:
        middle = (low_arm + high_arm) // 2
        if enough(2 * middle):
            high_arm = middle
        else:
            low_arm = middle + 1
    return 2 * low_arm


def _file_drawer(studies: list[dict], supplied: dict | None) -> dict:
    if supplied is not None:
        completed, unreported = (
            _number(supplied.get("completed")),
            _number(supplied.get("unreported")),
        )
        if (
            completed is not None
            and unreported is not None
            and completed >= 0
            and 0 <= unreported <= completed
            and completed.is_integer()
            and unreported.is_integer()
        ):
            result = {
                "completed": int(completed),
                "unreported": int(unreported),
                "share": unreported / completed if completed else None,
            }
            overdue = _number(supplied.get("overdue"))
            if overdue is not None and overdue.is_integer() and 0 <= overdue <= unreported:
                result["overdue"] = int(overdue)
            return result
    completed_studies = [
        study
        for study in studies
        if _is_registry(study) and str(study.get("overall_status", "")).upper() == "COMPLETED"
    ]
    overdue = sum(_unreported(study, datetime.now(UTC).date()) for study in completed_studies)
    unreported = sum(
        _unreported({**study, "primary_completion_date": "1900-01-01"}, date(2000, 1, 1))
        for study in completed_studies
    )
    return {
        "completed": len(completed_studies),
        "unreported": unreported,
        "overdue": overdue,
        "share": unreported / len(completed_studies) if completed_studies else None,
    }


def analyze_studies(
    studies: list[dict],
    plan: dict,
    file_drawer: dict | None = None,
    outcome_groups: dict[str, str] | None = None,
) -> dict:
    """Analyze retrieved studies; caller supplies corpus-wide file-drawer counts.

    ``outcome_groups`` maps study id to a shared outcome label for studies a language
    model judged comparable (same construct, same kind of comparison). It replaces only
    the exact-text match on outcome and comparison wording; scale, units for mean
    differences and every number are still checked and computed here, and such pools
    are marked so the judgment can be reviewed.

    Pools require >=3 independent primary studies with the same normalized effect
    type, outcome, outcome unit, intervention/comparator orientation and explicit
    direction metadata (when supplied). Missing outcomes, and MD units, preclude
    pooling. Population differences remain heterogeneity, not hidden conversions.
    When multiple pools match the request, no single assurance is selected.
    """
    assumptions = [
        "Study CIs are 95% unless a confidence level is supplied; SEs and power use normal approximations.",
        "SESOI is in SD units (SMD), outcome units (MD), or natural-log units (logOR/logRR/logHR).",
        (
            "Pooling requires at least three independent primary studies with matching outcome, scale, "
            "units, comparison and reported direction; semantic equivalence is not inferred."
        ),
        (
            "Assurance is expected probability of two-sided statistical significance, including harm; "
            "it is not the probability of meaningful benefit."
        ),
        (
            "The predictive true effect is Normal(pooled mean, tau² + pooled SE²); "
            "heterogeneity and pooled uncertainty are estimated from retrieved studies."
        ),
        (
            "Planned N is total enrollment split equally between two independent arms; "
            "no allowance is made for attrition, clustering or multiplicity."
        ),
        (
            "EV uses the supplied values' units; V_null is the value assigned to any nonsignificant "
            "result, which may still be inconclusive."
        ),
    ]
    warnings: list[str] = []
    labels: dict[str, str] = {}
    groups: dict[tuple, list] = defaultdict(list)
    seen: set[str] = set()
    unique_studies = []
    classifications: list[dict] = []
    ordered_studies = sorted(studies, key=lambda row: row.get("source") not in {"ctgov", "merged"})
    for position, study in enumerate(ordered_studies):
        sid = _study_id(study, position)
        aliases = {f"id:{sid}"}
        fields = (
            ("nct_ids",) if study.get("source") in {"ctgov", "merged"} else ("nct_ids", "pmids")
        )
        for field in fields:
            values = study.get(field) or []
            if isinstance(values, str):
                values = [values]
            aliases.update(f"{field}:{value}" for value in values)
        if aliases & seen:
            seen.update(aliases)
            warnings.append("Duplicate study identifiers were counted once.")
            continue
        seen.update(aliases)
        unique_studies.append(study)
        classification = assign_bucket(study, plan.get("sesoi", 0.2), plan.get("effectType", "SMD"))
        if study.get("is_review"):
            continue
        classifications.append(classification)
        if classification["bucket"] in {"failed", "unreported"}:
            continue
        if classification["analysis_se"] is None or classification["analysis_estimate"] is None:
            continue
        kind = classification["analysis_effect_type"]
        outcome = _text(study.get("outcome"))
        derived = classification["evidence_tier"] == "derived"
        # A derived SMD is in SD units whatever instrument the arms were measured on.
        unit = "SD" if derived and kind == "SMD" else _unit(study, kind)
        if not outcome or (kind == "MD" and not unit):
            warnings.append(
                "Numeric studies with unknown outcomes or missing MD units were not pooled."
            )
            continue
        if classification["evidence_tier"] == "reconstructed":
            warnings.append(
                "Some pooled evidence uses reconstructed normal-approximation uncertainty."
            )
        if derived:
            warnings.append(
                "Some pooled evidence uses effect sizes computed from reported arm-level summaries."
            )
        direction = _text(study.get("effect_direction") or study.get("outcome_direction"))
        comparison = (_text(study.get("intervention")), _text(study.get("comparator")))
        label = (outcome_groups or {}).get(sid)
        if label:
            # Different instruments for one construct share SD units, never raw units.
            if kind == "SMD":
                unit = "SD"
            outcome, comparison = f"group:{_text(label)}", ("", "")
            labels[outcome] = label
        if not direction:
            assumptions.append(
                "Within each comparison, effects retain their reported signs; "
                "consistent outcome coding is assumed where direction metadata is absent."
            )
        key = (kind, outcome, unit, *comparison, direction)
        groups[key].append((study, classification, sid))

    pools = []
    for key, group in groups.items():
        name = labels.get(key[1], key[1])
        if len(group) >= 3:
            pool = _pool(group, key)
            if key[1] in labels:
                pool.update(outcome=name, grouping="model")
                warnings.append(
                    f"Studies pooled under “{name}” were judged comparable by a language model "
                    "from their outcome and comparison wording; check the grouped studies."
                )
            pools.append(pool)
        else:
            warnings.append(
                f"Only {len(group)} compatible numeric studies for {name} ({key[0]}); "
                "at least 3 are required for pooling."
            )
    pools.sort(key=lambda pool: (-pool["k"], pool["outcome"], pool["effectType"]))
    if not pools:
        warnings.append(
            "No eligible meta-analysis pool; assurance and expected value are unavailable."
        )
    if any(pool["egger"] for pool in pools):
        assumptions.append(
            "Egger's test regresses each study's estimate/SE on its precision 1/SE; a nonzero "
            "intercept is funnel asymmetry, which publication bias, small-study effects or "
            "heterogeneity can all produce. No pooled estimate is adjusted for it."
        )
    for pool in pools:
        egger = pool["egger"]
        if egger is None:
            warnings.append(
                f"Funnel asymmetry was not tested for {pool['outcome']} ({pool['effectType']}): "
                f"Egger's test needs at least {EGGER_MIN_STUDIES} studies of differing precision "
                f"and this pool has {pool['k']}."
            )
        elif egger["asymmetric"]:
            warnings.append(
                f"Egger's test indicates funnel asymmetry for {pool['outcome']} "
                f"({pool['effectType']}): intercept {egger['intercept']:.2f} "
                f"(p = {egger['pValue']:.3f}); small studies report systematically different "
                "effects, so the pooled estimate may reflect selective reporting."
            )
    drawer = _file_drawer(unique_studies, file_drawer)
    if file_drawer is None:
        warnings.append(
            "File-drawer counts cover supplied studies only, not the full search corpus."
        )
    assumptions.append(
        "File drawer counts completed registered trials with no posted or linked results "
        "at any age, divided by all completed registered trials; overdue separately counts "
        "those more than 12 calendar months after completion. Registry coverage is incomplete "
        "and does not prove nonpublication."
    )
    if drawer["unreported"]:
        warnings.append(
            "Unreported completed trials are absent from the numeric pool; publication bias "
            "may affect pooled estimates and assurance."
        )

    pursuit = _pursuit(
        classifications,
        pools,
        _margin(plan.get("sesoi", 0.2), plan.get("effectType", "SMD")),
        _analysis_type(plan.get("effectType", "SMD")),
    )
    if pursuit["state"] == "contested":
        warnings.append(
            f"The record is contested: {pursuit['conflict']:.0%} of the informative evidence "
            "weight sits on the minority side. A posterior near one half here means the studies "
            "disagree, not that the question is half-settled; look for moderators before pooling."
        )
    assumptions.append(
        "The chance a real effect exists starts from a Beta(1, 1) prior (50/50) and counts each "
        "effect verdict as a success and each credible or reported null as a failure, weighted "
        "1 for quoted numbers, 0.75 for reconstructed uncertainty and 0.5 for text-only claims. "
        "Inconclusive, stopped and unreported records do not move it. Studies are assumed "
        "exchangeable, so it is a summary of the record, not a probability of clinical benefit."
    )

    result = {
        "pools": pools,
        "pursuit": pursuit,
        "assurance": None,
        "requiredN": None,
        "expectedValue": None,
        "plannedMde": None,
        "plannedNullBucket": None,
        "plannedNullCi": None,
        "fileDrawer": drawer,
        "assumptions": assumptions,
        "warnings": warnings,
        "assuranceDefinition": "Probability of two-sided statistical significance (either direction)",
        "requiredNTarget": 0.8,
        "selectedPoolStudyIds": [],
    }
    kind = _analysis_type(plan.get("effectType", "SMD"))
    factor = _variance_factor(kind, plan, warnings)
    alpha = _number(plan.get("alpha", 0.05))
    n = _number(plan.get("plannedN", 200))
    if alpha is None or not 0 < alpha < 1 or alpha / 2 == 0:
        warnings.append("Alpha must lie strictly between 0 and 1.")
        factor = None
    if n is None or n < 4 or not n.is_integer():
        warnings.append("Planned N must be an integer total sample size of at least 4.")
        factor = None
    if factor is not None and (not math.isfinite(factor) or factor <= 0):
        warnings.append("Design variance is outside usable numerical precision.")
        factor = None
    planned_se = None
    if factor is not None:
        if kind == "SMD":
            assumptions.append(
                "SMD planning uses SE = sqrt(1/n_intervention + 1/n_control), "
                "assuming known common outcome SD; this is a large-sample approximation."
            )
        elif kind == "MD":
            assumptions.append(
                "MD planning assumes the supplied outcomeSd is known and common to both arms."
            )
        elif kind in {"logOR", "logRR"}:
            assumptions.append(
                "Binary ratio planning uses a fixed delta-method variance at the supplied "
                "baseline risk in both arms; it is a local approximation near no effect "
                "and may be inaccurate for rare events or large effects."
            )
        # For odd total N, explicitly use the actual nearly equal arm sizes.
        arm1 = int(n) // 2
        arm2 = int(n) - arm1
        planned_se = math.sqrt(factor / 4 * (1 / arm1 + 1 / arm2))
        z = -_NORMAL.inv_cdf(alpha / 2)
        result["plannedMde"] = (z + _NORMAL.inv_cdf(0.8)) * planned_se
        margin = _margin(plan.get("sesoi", 0.2), plan.get("effectType", "SMD"))
        if margin is not None:
            # Equivalence classification always uses 95% CIs, regardless of test alpha.
            half_width = _Z95 * planned_se
            result["plannedNullCi"] = [-half_width, half_width]
            result["plannedNullBucket"] = "credible_null" if half_width < margin else "inconclusive"
        candidates = [pool for pool in pools if pool["effectType"] == kind]
        if plan.get("outcome"):
            candidates = [
                pool for pool in candidates if _text(pool["outcome"]) == _text(plan["outcome"])
            ]
        if plan.get("outcomeUnit"):
            target_unit = _unit({"outcome_unit": plan["outcomeUnit"]}, kind)
            candidates = [pool for pool in candidates if pool["unit"] == target_unit]
        if len(candidates) == 1:
            pool = candidates[0]
            assurance = _assurance(
                pool["estimate"], pool["tau2"] + pool["se"] ** 2, planned_se, alpha
            )
            result.update(
                assurance=assurance,
                requiredN=_required_n(pool, factor, alpha),
                selectedPoolStudyIds=pool["studyIds"],
            )
            if result["requiredN"] is None:
                warnings.append(
                    f"80% assurance was not reached by the search cap of {_MAX_REQUIRED_N:,} total participants."
                )
            values = [
                _number(plan.get(key, default))
                for key, default in (("valueSuccess", 100), ("valueNull", 20), ("studyCost", 30))
            ]
            if all(value is not None for value in values):
                result["expectedValue"] = (
                    assurance * values[0] + (1 - assurance) * values[1] - values[2]
                )
            else:
                warnings.append(
                    "Expected value needs finite valueSuccess, valueNull and studyCost."
                )
        elif len(candidates) > 1:
            warnings.append(
                "Multiple compatible pools match the plan; specify one outcome/unit/comparison "
                "before interpreting a single assurance or EV."
            )
        elif pools:
            warnings.append("No pooled outcome and effect scale match the planned study.")
    _decide(
        pursuit,
        planned_se,
        _margin(plan.get("sesoi", 0.2), plan.get("effectType", "SMD")),
        alpha if factor is not None else None,
        drawer,
    )
    assumptions.append(
        "The chance you should pursue the study is the chance the record's lean is wrong "
        "(twice the smaller posterior tail around even odds) times the planned design's power "
        "to detect the SESOI. It weighs whether a new study would change the answer, not the "
        "value of the answer; the expected value below carries the supplied values."
    )
    result["assumptions"] = list(dict.fromkeys(assumptions))
    result["warnings"] = list(dict.fromkeys(warnings))
    return result
