"""Validated public requests and evidence-constrained LLM schemas."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Bucket = Literal[
    "effect", "credible_null", "reported_null", "inconclusive", "failed", "unreported"
]
EffectType = Literal["SMD", "MD", "logOR", "logRR", "logHR"]


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)
    idea: str = Field(min_length=8, max_length=12000)
    field: str | None = Field(default=None, max_length=100)
    sesoi: float | None = Field(default=None, gt=0, le=1000)
    effectType: EffectType = "SMD"
    plannedN: int = Field(default=200, ge=4, le=1000000)
    alpha: float = Field(default=0.05, gt=0, lt=0.5)
    valueSuccess: float = Field(default=100, ge=0, le=1e12)
    valueNull: float = Field(default=20, ge=0, le=1e12)
    studyCost: float = Field(default=30, ge=0, le=1e12)
    outcomeSd: float | None = Field(default=None, gt=0, le=1e6)
    baselineRisk: float | None = Field(default=None, gt=0, lt=1)


class Pico(BaseModel):
    population: str
    intervention: str
    comparator: str
    outcome: str
    synonyms: list[str]
    interventionAliases: list[str] = Field(default_factory=list)
    outcomeAliases: list[str] = Field(default_factory=list)
    populationAliases: list[str] = Field(default_factory=list)
    studyDesigns: list[str]
    sesoi: float = Field(gt=0)
    sesoiRationale: str
    effectType: EffectType


class NumberEvidence(BaseModel):
    value: float
    quote: str


class TextEvidence(BaseModel):
    value: str
    quote: str


class ResultEvidence(BaseModel):
    """What the report itself states for its primary between-group comparison."""

    value: Literal["positive", "null", "mixed"]
    quote: str


Direction = Literal["favours_intervention", "favours_comparator", "unclear"]


class DirectionEvidence(BaseModel):
    """Which arm the reported primary result favours; significance is a separate fact."""

    value: Direction
    quote: str


class BoolEvidence(BaseModel):
    value: bool
    quote: str


class Extraction(BaseModel):
    """Every returned fact must have verbatim support in the ORIGINAL abstract."""

    population: TextEvidence | None
    intervention: TextEvidence | None
    comparator: TextEvidence | None
    outcome: TextEvidence | None
    outcome_unit: TextEvidence | None
    design: TextEvidence | None
    n: NumberEvidence | None
    n_intervention: NumberEvidence | None
    n_comparator: NumberEvidence | None
    mean_intervention: NumberEvidence | None
    mean_comparator: NumberEvidence | None
    sd_intervention: NumberEvidence | None
    sd_comparator: NumberEvidence | None
    events_intervention: NumberEvidence | None
    events_comparator: NumberEvidence | None
    estimate: NumberEvidence | None
    ci_low: NumberEvidence | None
    ci_high: NumberEvidence | None
    ci_level: NumberEvidence | None
    ci_sides: TextEvidence | None
    p_value: NumberEvidence | None
    effect_type: TextEvidence | None
    primary_outcome_met: BoolEvidence | None
    reported_result: ResultEvidence | None
    result_direction: DirectionEvidence | None
    has_control: BoolEvidence | None


class Relevance(BaseModel):
    """Ids of retrieved studies that actually address the research question."""

    relevant_ids: list[str]


class OutcomeGroup(BaseModel):
    label: str
    study_ids: list[str]


class OutcomeGroups(BaseModel):
    """Studies judged to measure the same outcome for the same kind of comparison."""

    groups: list[OutcomeGroup]


class EffectTrend(BaseModel):
    summary: str
    patterns: list[str]


class Narrative(BaseModel):
    summary: str
    drivers: list[str]


class IndexedNumberEvidence(BaseModel):
    value: float
    sentence_index: int = Field(ge=0)


class IndexedTextEvidence(BaseModel):
    value: str
    sentence_index: int = Field(ge=0)


class IndexedResultEvidence(BaseModel):
    value: Literal["positive", "null", "mixed"]
    sentence_index: int = Field(ge=0)


class IndexedDirectionEvidence(BaseModel):
    value: Direction
    sentence_index: int = Field(ge=0)


class IndexedBoolEvidence(BaseModel):
    value: bool
    sentence_index: int = Field(ge=0)


class IndexedExtraction(BaseModel):
    """Select an original sentence; application code supplies its exact quote."""

    population: IndexedTextEvidence | None
    intervention: IndexedTextEvidence | None
    comparator: IndexedTextEvidence | None
    outcome: IndexedTextEvidence | None
    outcome_unit: IndexedTextEvidence | None
    design: IndexedTextEvidence | None
    n: IndexedNumberEvidence | None
    n_intervention: IndexedNumberEvidence | None
    n_comparator: IndexedNumberEvidence | None
    mean_intervention: IndexedNumberEvidence | None
    mean_comparator: IndexedNumberEvidence | None
    sd_intervention: IndexedNumberEvidence | None
    sd_comparator: IndexedNumberEvidence | None
    events_intervention: IndexedNumberEvidence | None
    events_comparator: IndexedNumberEvidence | None
    estimate: IndexedNumberEvidence | None
    ci_low: IndexedNumberEvidence | None
    ci_high: IndexedNumberEvidence | None
    ci_level: IndexedNumberEvidence | None
    ci_sides: IndexedTextEvidence | None
    p_value: IndexedNumberEvidence | None
    effect_type: IndexedTextEvidence | None
    primary_outcome_met: IndexedBoolEvidence | None
    reported_result: IndexedResultEvidence | None
    result_direction: IndexedDirectionEvidence | None
    has_control: IndexedBoolEvidence | None
