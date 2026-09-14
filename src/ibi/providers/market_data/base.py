"""The `MarketDataProvider` interface.

Kept intentionally minimal in Phase 0 — just enough shape (a price-bar query)
to prove the abstraction, not a full market-data API surface. `market_engine`
will extend the request/response types (volume, corporate actions, etc.) as
it is actually built; this module should not be pre-designed further ahead
of that real usage.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ibi.core.types import Uncertain


@dataclass(frozen=True, slots=True)
class PriceQuery:
    symbol: str
    start_date: date
    end_date: date


@dataclass(frozen=True, slots=True)
class PriceBar:
    symbol: str
    as_of: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


class MarketDataProvider(ABC):
    """Contract every market-data backend must implement.

    ``get_price_history`` returns ``Uncertain`` (never a fabricated series)
    when the provider cannot answer — see `ibi.core.types`.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable provider identifier used in logs and stored data provenance."""

    @abstractmethod
    def get_price_history(self, query: PriceQuery) -> list[PriceBar] | Uncertain:
        """Return daily price bars for the query window, or an `Uncertain` value."""
