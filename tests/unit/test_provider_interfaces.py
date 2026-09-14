"""Provider abstractions must be swappable: domain code should be able to
depend on `AIProvider`/`MarketDataProvider` without knowing which concrete
implementation is behind them, and the default (no vendor configured) must
be safe rather than silently working."""

from __future__ import annotations

from datetime import date

import pytest

from ibi.config import Settings
from ibi.core.errors import ConfigurationError, ProviderError
from ibi.core.types import Unknown
from ibi.providers.ai.base import AIProvider, AIRequest, ModelTier
from ibi.providers.ai.null_provider import NullAIProvider
from ibi.providers.ai.router import build_ai_provider
from ibi.providers.market_data.base import MarketDataProvider, PriceQuery
from ibi.providers.market_data.null_provider import NullMarketDataProvider
from ibi.providers.market_data.router import build_market_data_provider


def test_null_ai_provider_is_an_ai_provider():
    assert isinstance(NullAIProvider(), AIProvider)


def test_null_ai_provider_refuses_to_complete():
    provider = NullAIProvider()
    with pytest.raises(ProviderError):
        provider.complete(AIRequest(prompt="hello", tier=ModelTier.LOW))


def test_router_returns_null_provider_by_default():
    settings = Settings(_env_file=None, ai_provider="none")
    provider = build_ai_provider(settings)
    assert provider.name == "none"


def test_router_raises_configuration_error_for_claude_without_key():
    settings = Settings(_env_file=None, ai_provider="claude")
    with pytest.raises(RuntimeError):
        build_ai_provider(settings)


def test_null_market_data_provider_is_a_market_data_provider():
    assert isinstance(NullMarketDataProvider(), MarketDataProvider)


def test_null_market_data_provider_returns_unknown_not_fabricated_data():
    provider = NullMarketDataProvider()
    result = provider.get_price_history(
        PriceQuery(symbol="ACME", start_date=date(2024, 1, 1), end_date=date(2024, 1, 31))
    )
    assert isinstance(result, Unknown)


def test_market_data_router_rejects_unimplemented_other_provider():
    settings = Settings(_env_file=None, market_data_provider="other")
    with pytest.raises(ConfigurationError):
        build_market_data_provider(settings)
