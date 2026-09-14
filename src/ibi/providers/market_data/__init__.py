"""Market/reference data provider abstraction.

Mirrors `providers.ai`: domain engines depend on `base.MarketDataProvider`,
never on a vendor SDK. No production market-data vendor is selected in
Phase 0 — `NullMarketDataProvider` is the only implementation, and it always
returns `Unknown` rather than fabricated data.
"""

from ibi.providers.market_data.base import MarketDataProvider, PriceBar, PriceQuery

__all__ = ["MarketDataProvider", "PriceBar", "PriceQuery"]
