"""Configuration must validate, and must never let a Claude call proceed
without an explicit API key — see ibi/config.py."""

from __future__ import annotations

import pytest

from ibi.config import Settings, get_settings


def test_defaults_are_deterministic_only(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("IBI_AI_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = Settings(_env_file=None)
    assert settings.ai_provider == "none"
    assert settings.market_data_provider == "null"


def test_require_anthropic_api_key_raises_when_provider_is_not_claude():
    settings = Settings(_env_file=None, ai_provider="none")
    with pytest.raises(RuntimeError, match="IBI_AI_PROVIDER"):
        settings.require_anthropic_api_key()


def test_require_anthropic_api_key_raises_when_key_missing():
    settings = Settings(_env_file=None, ai_provider="claude")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        settings.require_anthropic_api_key()


def test_require_anthropic_api_key_succeeds_when_configured():
    settings = Settings(_env_file=None, ai_provider="claude", anthropic_api_key="sk-test")
    assert settings.require_anthropic_api_key().get_secret_value() == "sk-test"


def test_negative_budget_is_rejected():
    with pytest.raises(ValueError):
        Settings(_env_file=None, ai_monthly_budget_usd=-1)


def test_get_settings_is_cached():
    assert get_settings() is get_settings()
