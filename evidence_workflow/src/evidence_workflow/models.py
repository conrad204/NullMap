from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class EvidenceTier(str, Enum):
    FULL = "full_quant"
    RECONSTRUCTED = "p_reconstructed"
    QUALITATIVE = "qualitative"
    UNEXTRACTABLE = "unextractable"
    FAILED = "methodologically_failed"
    UNREPORTED = "completed_unreported"


class Finding(str, Enum):
    BENEFIT = "benefit"
    HARM = "harm"
    EQUIVALENT = "clinically_equivalent"
    INCONCLUSIVE = "inconclusive"
    MIXED = "mixed"
    FAILED = "methodologically_failed"
    UNREPORTED = "completed_unreported"
    UNCLASSIFIABLE = "unclassifiable"


class RiskOfBias(str, Enum):
    LOW = "low"
    SOME_CONCERNS = "some_concerns"
    HIGH = "high"
    UNKNOWN = "unknown"


@dataclass
class Study:
    study_id: str
    cluster_id: str
    year: int | None = None
    design: str | None = None
    population: str | None = None
    intervention: str | None = None
    comparator: str | None = None
    outcome: str | None = None
    timepoint: str | None = None
    measure: str | None = None
    scale_id: str | None = None
    higher_is_better: bool = True

    effect: float | None = None
    se: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    ci_level: float = 0.95

    p_value: float | None = None
    p_operator: str = "="
    test_type: str | None = None
    two_sided: bool = True
    direction: int | None = None
    n1: int | None = None
    n2: int | None = None
    df: float | None = None

    qualitative_label: str | None = None
    qualitative_text: str | None = None
    risk_of_bias: str = RiskOfBias.UNKNOWN.value
    methodologically_failed: bool = False
    completed_unreported: bool = False
    pico_compatible: bool = True
    primary_result: bool = True
    notes: list[str] = field(default_factory=list)

    tier: str | None = None
    finding: str | None = None
    analysis_effect: float | None = None
    variance: float | None = None
    reconstructed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AnalysisConfig:
    alpha: float = 0.05
    equivalence_alpha: float = 0.05
    sesoi_low: float = -0.2
    sesoi_high: float = 0.2
    min_studies_for_pooling: int = 2
    target_precision_fraction: float = 0.5
    resolved_score_threshold: float = 80.0
    high_i2_flag: float = 0.60

    def validate(self) -> None:
        if not 0 < self.alpha < 1:
            raise ValueError("alpha must be in (0, 1)")
        if not 0 < self.equivalence_alpha < 0.5:
            raise ValueError("equivalence_alpha must be in (0, 0.5)")
        if self.sesoi_low >= self.sesoi_high:
            raise ValueError("SESOI lower bound must be below upper bound")
        if not 0 < self.target_precision_fraction <= 1:
            raise ValueError("target_precision_fraction must be in (0, 1]")
