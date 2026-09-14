"""Decision states and the (unimplemented) engine contract.

`DecisionState` intentionally has no BUY/SELL member. `INSUFFICIENT_EVIDENCE`
and `CONFLICTING_EVIDENCE` are first-class outcomes, not error conditions —
mirroring `ibi.core.types.Uncertain` at the decision level, because "we
don't have enough to decide" is frequently the correct output of a serious
research process.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class DecisionState(StrEnum):
    STRONG_OPPORTUNITY = "strong_opportunity"
    OPPORTUNITY = "opportunity"
    WATCH = "watch"
    NEUTRAL = "neutral"
    AVOID = "avoid"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    CONFLICTING_EVIDENCE = "conflicting_evidence"


@dataclass(frozen=True, slots=True)
class Decision:
    entity_id: str
    state: DecisionState
    rationale: str
    """Human-readable explanation. Must reference the evidence/thesis/score
    that produced it (traceability) rather than assert the state bare."""
    as_of: datetime
    supporting_thesis_id: str | None = None
    supporting_score_id: str | None = None


class DecisionEngine(ABC):
    """Not implemented in Phase 0. Combines scoring, valuation, thesis, and
    red-team output into a `Decision`; the combination policy is Phase 1+ work."""

    @abstractmethod
    def decide(self, entity_id: str) -> Decision: ...
