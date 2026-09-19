from __future__ import annotations

import math

from scipy import stats

from .models import Study


SUPPORTED_TESTS = {
    "two_sample_t",
    "one_sample_t",
    "paired_t",
    "correlation_t",
    "chi_square_1df",
}


def _signed_statistic(p: float, distribution, two_sided: bool, direction: int) -> float:
    tail = p / 2 if two_sided else p
    magnitude = float(distribution.ppf(1 - tail))
    return direction * magnitude


def reconstruct_from_exact_p(study: Study) -> tuple[float, float, str]:
    """Recover an approximate standardized effect and sampling variance.

    This is allowed only for an exact p-value and a recognized test. Sample size
    alone is not enough: the test family, sidedness, direction, and degrees of
    freedom (when applicable) are part of the statistical result.
    """
    if study.p_operator != "=":
        raise ValueError("Censored p-values (for example p<0.05) are not point estimates")
    if study.p_value is None or not 0 < study.p_value <= 1:
        raise ValueError("An exact p-value in (0, 1] is required")
    if study.direction not in (-1, 1):
        raise ValueError("direction must be -1 or +1")
    if study.test_type not in SUPPORTED_TESTS:
        raise ValueError(f"unsupported or missing test_type: {study.test_type!r}")

    test = study.test_type
    if test == "two_sample_t":
        if not study.n1 or not study.n2:
            raise ValueError("two_sample_t requires n1 and n2")
        df = study.df if study.df is not None else study.n1 + study.n2 - 2
        t = _signed_statistic(study.p_value, stats.t(df), study.two_sided, study.direction)
        d = t * math.sqrt(1 / study.n1 + 1 / study.n2)
        correction = 1 - 3 / (4 * df - 1) if df > 1 else 1.0
        g = correction * d
        var_g = ((study.n1 + study.n2) / (study.n1 * study.n2) + d * d / (2 * df)) * correction**2
        return g, var_g, "hedges_g"

    if test in {"one_sample_t", "paired_t"}:
        if not study.n1:
            raise ValueError(f"{test} requires n1")
        df = study.df if study.df is not None else study.n1 - 1
        t = _signed_statistic(study.p_value, stats.t(df), study.two_sided, study.direction)
        dz = t / math.sqrt(study.n1)
        var = 1 / study.n1 + dz * dz / (2 * max(df, 1))
        return dz, var, "cohen_dz"

    if test == "correlation_t":
        if not study.n1 or study.n1 <= 3:
            raise ValueError("correlation_t requires n1 > 3")
        df = study.df if study.df is not None else study.n1 - 2
        t = _signed_statistic(study.p_value, stats.t(df), study.two_sided, study.direction)
        r = math.copysign(math.sqrt(t * t / (t * t + df)), t)
        return math.atanh(r), 1 / (study.n1 - 3), "fisher_z"

    if not study.n1:
        raise ValueError("chi_square_1df requires total n in n1")
    chi2 = float(stats.chi2(df=1).ppf(1 - study.p_value))
    phi = study.direction * math.sqrt(chi2 / study.n1)
    # Delta-method approximation; clearly marked reconstructed downstream.
    return phi, 1 / study.n1, "phi"

