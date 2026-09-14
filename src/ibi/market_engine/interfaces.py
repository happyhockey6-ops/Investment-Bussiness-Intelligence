"""The `MarketSignalCalculator` contract.

Deliberately mirrors `financial_engine.interfaces.MetricCalculation`: a
market signal (volatility, drawdown, momentum, ...) is also expected to be
a deterministic function of price/volume history, not an AI judgment call.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal

from ibi.core.types import Uncertain
from ibi.providers.market_data.base import PriceBar


class MarketSignalCalculator(ABC):
    """Not implemented in Phase 0."""

    signal_id: str

    @abstractmethod
    def compute(self, bars: list[PriceBar]) -> Decimal | Uncertain: ...
