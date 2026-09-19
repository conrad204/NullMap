"""Pydantic v2 schema for the negative-priors engine."""

from __future__ import annotations

from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

OutcomeType = Literal["null", "negative", "positive", "inconclusive"]
Origin = Literal["internal", "openalex"]


def coerce_number(value: str) -> float | str:
    text = value.strip().rstrip(".")
    try:
        return float(text)
    except ValueError:
        return text


class Experiment(BaseModel):
    """One experimental run, whether from a lab notebook or a published paper."""

    experiment_id: str = Field(default_factory=lambda: f"exp-{uuid4().hex[:12]}")
    source: str
    hypothesis: str
    independent_vars: dict[str, float | str] = Field(default_factory=dict)
    dependent_vars: dict[str, float | str] = Field(default_factory=dict)
    outcome_type: OutcomeType
    effect_size: float | None = None
    p_value: float | None = None
    summary: str
    embedding: list[float] | None = None

    @field_validator("p_value")
    @classmethod
    def _p_in_unit_interval(cls, v: float | None) -> float | None:
        if v is not None and not 0.0 <= v <= 1.0:
            raise ValueError(f"p_value must be in [0, 1], got {v}")
        return v

    def text_for_embedding(self) -> str:
        return f"{self.hypothesis}\n{self.summary}"


class ExperimentDraft(BaseModel):
    """What the extractor is asked to produce: `Experiment` minus the machine-owned fields.

    Kept separate so an LLM is never asked to invent an id or an embedding vector.
    """

    hypothesis: str
    independent_vars: dict[str, float | str] = Field(default_factory=dict)
    dependent_vars: dict[str, float | str] = Field(default_factory=dict)
    outcome_type: OutcomeType
    effect_size: float | None = None
    p_value: float | None = None
    summary: str


class VarReading(BaseModel):
    """One variable as an LLM reports it: a flat pair, since strict JSON schemas reject free-form maps."""

    name: str
    value: str


class ExperimentExtraction(BaseModel):
    """The strict-mode response_format: every field required, no defaults, no open dicts."""

    hypothesis: str
    independent_vars: list[VarReading]
    dependent_vars: list[VarReading]
    outcome_type: OutcomeType
    effect_size: float | None
    p_value: float | None
    summary: str

    def to_draft(self) -> ExperimentDraft:
        return ExperimentDraft(
            hypothesis=self.hypothesis,
            independent_vars={v.name: coerce_number(v.value) for v in self.independent_vars},
            dependent_vars={v.name: coerce_number(v.value) for v in self.dependent_vars},
            outcome_type=self.outcome_type,
            effect_size=self.effect_size,
            p_value=self.p_value,
            summary=self.summary,
        )


class PriorRisk(BaseModel):
    """A prior attempt that bears on a proposed protocol."""

    origin: Origin
    ref: str
    title: str
    outcome_type: OutcomeType
    score: float
    year: int | None = None
    p_value: float | None = None
    effect_size: float | None = None
    url: str | None = None
    snippet: str = ""


class PriorCheck(BaseModel):
    """The verdict for one proposed protocol."""

    query: str
    matches: list[PriorRisk] = Field(default_factory=list)
    risk_score: float = 0.0
    verdict: str = ""

    @property
    def internal(self) -> list[PriorRisk]:
        return [m for m in self.matches if m.origin == "internal"]

    @property
    def published(self) -> list[PriorRisk]:
        return [m for m in self.matches if m.origin == "openalex"]
