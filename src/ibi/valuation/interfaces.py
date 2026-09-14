"""The `ValuationApproach` contract.

No approach is implemented in Phase 0 (DCF, comparable multiples, dividend
discount, asset-based, etc. are all Phase 1+ work). This module exists so
`entity_engine`/`scoring` can be built against a stable interface, and so
`ApproachApplicability` gives every future approach a documented, explicit
way to say "I should not be applied to this entity" instead of silently
producing a number for an inappropriate asset class.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from ibi.core.epistemics import EpistemicLabel
from ibi.core.types import Uncertain


class ValuationApproachKind(StrEnum):
    DCF = "dcf"
    FREE_CASH_FLOW_MULTIPLE = "fcf_multiple"
    EARNINGS_MULTIPLE = "earnings_multiple"  # P/E
    REVENUE_MULTIPLE = "revenue_multiple"  # P/S
    EV_EBITDA = "ev_ebitda"
    EV_EBIT = "ev_ebit"
    PRICE_TO_BOOK = "price_to_book"
    DIVIDEND_DISCOUNT = "dividend_discount"
    ASSET_BASED = "asset_based"
    RELATIVE_TO_PEERS = "relative_to_peers"
    SECTOR_SPECIFIC = "sector_specific"


@dataclass(frozen=True, slots=True)
class ApproachApplicability:
    """Whether a given approach makes sense for a given entity, and why."""

    applicable: bool
    reason: str


@dataclass(frozen=True, slots=True)
class ValuationResult:
    approach: ValuationApproachKind
    implied_value_per_share: Decimal | Uncertain
    epistemic_label: EpistemicLabel
    """Almost always CALCULATION (deterministic formula on given assumptions)
    or ASSUMPTION-qualified — never FACT; a valuation is a model output."""
    assumptions: dict[str, str]
    """Human-readable record of every assumption fed into the model, so the
    result is auditable without re-deriving it."""


class ValuationApproach(ABC):
    """One way of estimating intrinsic/relative value for an entity."""

    kind: ValuationApproachKind

    @abstractmethod
    def applicability(self, entity_id: str) -> ApproachApplicability:
        """Must be checked before `value`; callers should not apply a DCF to
        an entity this returns `applicable=False` for."""

    @abstractmethod
    def value(self, entity_id: str) -> ValuationResult:
        """Compute the valuation. Implementations must not silently proceed
        on inapplicable entities — check `applicability` first."""
