from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np
from scipy import optimize, stats


@dataclass
class MetaResult:
    k: int
    mean: float
    se: float
    ci_low: float
    ci_high: float
    tau2: float
    q: float
    q_p_value: float
    i2: float
    prediction_low: float | None
    prediction_high: float | None
    total_information: float

    def to_dict(self) -> dict:
        return asdict(self)


def _reml_tau2(y: np.ndarray, v: np.ndarray) -> float:
    if len(y) < 2:
        return 0.0

    def objective(log_tau2: float) -> float:
        tau2 = math.exp(log_tau2)
        w = 1 / (v + tau2)
        mu = np.sum(w * y) / np.sum(w)
        return 0.5 * (
            np.sum(np.log(v + tau2))
            + math.log(np.sum(w))
            + np.sum(w * (y - mu) ** 2)
        )

    upper = max(float(np.var(y, ddof=1) * 100), float(np.max(v) * 100), 1.0)
    fit = optimize.minimize_scalar(
        objective,
        bounds=(math.log(1e-12), math.log(upper)),
        method="bounded",
    )
    return max(0.0, float(math.exp(fit.x)))


def random_effects_meta(effects: list[float], variances: list[float], alpha: float = 0.05) -> MetaResult:
    y = np.asarray(effects, dtype=float)
    v = np.asarray(variances, dtype=float)
    if len(y) == 0 or len(y) != len(v):
        raise ValueError("effects and variances must have the same non-zero length")
    if np.any(~np.isfinite(y)) or np.any(~np.isfinite(v)) or np.any(v <= 0):
        raise ValueError("effects must be finite and variances must be finite and positive")

    fixed_w = 1 / v
    fixed_mean = float(np.sum(fixed_w * y) / np.sum(fixed_w))
    q = float(np.sum(fixed_w * (y - fixed_mean) ** 2))
    q_p = float(1 - stats.chi2.cdf(q, max(len(y) - 1, 1))) if len(y) > 1 else float("nan")
    i2 = max(0.0, (q - (len(y) - 1)) / q) if len(y) > 1 and q > 0 else 0.0

    tau2 = _reml_tau2(y, v)
    w = 1 / (v + tau2)
    mean = float(np.sum(w * y) / np.sum(w))

    if len(y) >= 3:
        # Hartung-Knapp scale adjustment, bounded below by the conventional variance.
        hk_scale = float(np.sum(w * (y - mean) ** 2) / (len(y) - 1))
        se = math.sqrt(max(1.0, hk_scale) / np.sum(w))
        critical = float(stats.t.ppf(1 - alpha / 2, len(y) - 1))
    else:
        se = math.sqrt(1 / np.sum(w))
        critical = float(stats.norm.ppf(1 - alpha / 2))

    ci_low, ci_high = mean - critical * se, mean + critical * se
    if len(y) >= 3:
        pred_se = math.sqrt(tau2 + se * se)
        pred_critical = float(stats.t.ppf(1 - alpha / 2, len(y) - 2))
        pred_low, pred_high = mean - pred_critical * pred_se, mean + pred_critical * pred_se
    else:
        pred_low = pred_high = None

    return MetaResult(
        k=len(y), mean=mean, se=se, ci_low=ci_low, ci_high=ci_high,
        tau2=tau2, q=q, q_p_value=q_p, i2=i2,
        prediction_low=pred_low, prediction_high=pred_high,
        total_information=float(np.sum(w)),
    )


def zone_probabilities(mean: float, sd: float, low: float, high: float) -> dict[str, float]:
    if sd <= 0 or low >= high:
        raise ValueError("sd must be positive and low must be below high")
    p_harm = float(stats.norm.cdf(low, loc=mean, scale=sd))
    p_benefit = float(1 - stats.norm.cdf(high, loc=mean, scale=sd))
    p_null = max(0.0, 1 - p_harm - p_benefit)
    return {"harm": p_harm, "clinically_negligible": p_null, "benefit": p_benefit}


def normalized_resolution(probabilities: dict[str, float]) -> float:
    p = np.asarray(list(probabilities.values()), dtype=float)
    p = p[p > 0]
    entropy = -float(np.sum(p * np.log(p)))
    return max(0.0, min(1.0, 1 - entropy / math.log(3)))

