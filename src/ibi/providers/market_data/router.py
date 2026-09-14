"""Factory that turns `Settings.market_data_provider` into a `MarketDataProvider`.

Kept separate from `providers.ai.router` even though the pattern is
identical, because the two providers evolve independently and a future
"other" vendor here has no relationship to AI model routing.
"""

from __future__ import annotations

from ibi.config import Settings
from ibi.core.errors import ConfigurationError
from ibi.providers.market_data.base import MarketDataProvider
from ibi.providers.market_data.null_provider import NullMarketDataProvider


def build_market_data_provider(settings: Settings) -> MarketDataProvider:
    if settings.market_data_provider == "null":
        return NullMarketDataProvider()
    raise ConfigurationError(
        "IBI_MARKET_DATA_PROVIDER='other' has no concrete implementation yet. "
        "Phase 0 deliberately does not select a production market-data vendor; "
        "add a new provider module and wire it here when one is chosen."
    )
