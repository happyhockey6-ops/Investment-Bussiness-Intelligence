"""The dynamic-weighting contract.

`ScoringContext` carries everything a weighting policy is allowed to
condition on. Adding a new field here is a deliberate architectural
decision (it becomes something every future policy can key off of); it
should not be extended casually.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum

from ibi.core.types import Uncertain


class InvestmentHorizon(StrEnum):
    SHORT_TERM = "short_term"
    MEDIUM_TERM = "medium_term"
    LONG_TERM = "long_term"


class MarketRegime(StrEnum):
    """Deliberately coarse in Phase 0 — real regime classification is
    `market_engine` work, not yet implemented."""

    UNKNOWN = "unknown"
    RISK_ON = "risk_on"
    RISK_OFF = "risk_off"
    HIGH_VOLATILITY = "high_volatility"


@dataclass(frozen=True, slots=True)
class ScoringContext:
    entity_id: str
    asset_class: str
    sector: str | None
    industry: str | None
    business_model: str | None
    horizon: InvestmentHorizon
    market_regime: MarketRegime
    data_completeness: float
    """Fraction in [0, 1] of the inputs a full score would want that are
    actually available; a policy should down-weight or refuse to score
    components it lacks data for rather than treating missing data as zero."""


@dataclass(frozen=True, slots=True)
class ScoreComponent:
    name: str
    weight: float
    value: float | Uncertain


@dataclass(frozen=True, slots=True)
class Score:
    entity_id: str
    components: tuple[ScoreComponent, ...]
    composite: float | Uncertain
    """`Uncertain` when data_completeness is too low for the composite to be
    meaningful — a policy is expected to refuse rather than compute a
    confident-looking number from thin data."""


class WeightingPolicy(ABC):
    """Decides component weights for a given context.

    Kept separate from `Scorer` so a weighting policy (e.g. "how a bank
    should be weighted differently from a SaaS company") can be swapped or
    A/B'd independently of how components are combined.
    """

    @abstractmethod
    def weights_for(self, context: ScoringContext) -> dict[str, float]:
        """Return component-name -> weight, summing to 1.0 over the
        components this policy considers relevant to `context`."""


class Scorer(ABC):
    @abstractmethod
    def score(self, context: ScoringContext) -> Score:
        """Combine components using a `WeightingPolicy` appropriate to `context`."""
