"""Scenario and thesis data contracts.

`InvalidationCondition` is the piece most designs skip and this one treats
as first-class: a thesis that cannot say, in advance, what evidence would
prove it wrong is not falsifiable, and `red_team` depends on these
conditions existing to have something concrete to check against.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from ibi.core.epistemics import EpistemicLabel


class ScenarioKind(StrEnum):
    BEAR = "bear"
    BASE = "base"
    BULL = "bull"


@dataclass(frozen=True, slots=True)
class InvalidationCondition:
    description: str
    """What observable evidence, if it occurred, would falsify this scenario."""
    checked_as_of: str | None = None  # ISO date string; None = never checked


@dataclass(frozen=True, slots=True)
class Scenario:
    kind: ScenarioKind
    assumptions: tuple[str, ...]
    catalysts: tuple[str, ...]
    risks: tuple[str, ...]
    unknowns: tuple[str, ...]
    invalidation_conditions: tuple[InvalidationCondition, ...]
    confidence: float  # 0.0-1.0, subjective probability this scenario plays out
    epistemic_label: EpistemicLabel = EpistemicLabel.INFERENCE

    def __post_init__(self) -> None:
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence must be in [0, 1], got {self.confidence!r}")


@dataclass(frozen=True, slots=True)
class Thesis:
    entity_id: str
    bear: Scenario
    base: Scenario
    bull: Scenario
    evidence_ids: tuple[str, ...] = field(default_factory=tuple)
    """References into the evidence store (`ibi.core.epistemics.Evidence` /
    `db.models.evidence`) this thesis was built from — required for
    traceability, not optional metadata."""

    def __post_init__(self) -> None:
        kinds = {self.bear.kind, self.base.kind, self.bull.kind}
        expected = {ScenarioKind.BEAR, ScenarioKind.BASE, ScenarioKind.BULL}
        if kinds != expected:
            raise ValueError("Thesis requires exactly one bear, one base, and one bull scenario")
