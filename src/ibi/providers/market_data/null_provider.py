"""Default `MarketDataProvider`: no vendor wired in, always honest about it.

Phase 0 must not hard-code a production market-data vendor. This provider
is what `IBI_MARKET_DATA_PROVIDER=null` resolves to, and it is what tests
and local development use by default — every call returns `Unknown` rather
than either fabricating data or raising, so calling code is forced to
handle "we have no data" as a normal, expected case from day one.
"""

from __future__ import annotations

from ibi.core.types import Unknown
from ibi.providers.market_data.base import MarketDataProvider, PriceBar, PriceQuery


class NullMarketDataProvider(MarketDataProvider):
    @property
    def name(self) -> str:
        return "null"

    def get_price_history(self, query: PriceQuery) -> list[PriceBar] | Unknown:
        return Unknown(reason="no market data provider is configured")
