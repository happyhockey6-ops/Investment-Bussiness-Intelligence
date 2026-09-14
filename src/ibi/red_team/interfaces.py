"""The contrarian-review contract.

A `RedTeamReviewer` takes a `Thesis` and tries to break it: attack its
assumptions, propose evidence that would invalidate a scenario, and surface
risks the original thesis underweighted. Output is always
`EpistemicLabel.INFERENCE` or weaker (see `ibi.core.epistemics`) — a
red-team finding is a hypothesis to investigate, not a verified fact.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ibi.core.epistemics import EpistemicLabel
from ibi.thesis_engine.interfaces import Thesis


@dataclass(frozen=True, slots=True)
class ContrarianFinding:
    target_assumption_or_claim: str
    challenge: str
    """The specific reason this assumption/claim may be wrong."""
    severity: float  # 0.0-1.0 subjective impact on the thesis if the challenge holds
    epistemic_label: EpistemicLabel = EpistemicLabel.INFERENCE


@dataclass(frozen=True, slots=True)
class RedTeamReview:
    thesis_entity_id: str
    findings: tuple[ContrarianFinding, ...]
    unresolved_questions: tuple[str, ...]
    """Questions the reviewer could not answer with available evidence —
    surfaced explicitly rather than silently dropped."""


class RedTeamReviewer(ABC):
    """Not implemented in Phase 0."""

    @abstractmethod
    def review(self, thesis: Thesis) -> RedTeamReview: ...
