"""Environment configuration.

All configuration enters the system through this module. Nothing else in the
codebase should call `os.environ` directly — that keeps required variables
centrally validated and makes it possible to see, in one place, every piece
of external configuration the platform depends on.

Secrets (API keys, database credentials) are read from the environment /
`.env` and are never given defaults that look like real values, so a missing
secret fails fast at startup instead of silently degrading.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "production"]
AIProviderName = Literal["claude", "local", "none"]
MarketDataProviderName = Literal["null", "other"]


class Settings(BaseSettings):
    """Validated process configuration, loaded once and shared read-only.

    Values are read from environment variables prefixed with ``IBI_`` (except
    ``ANTHROPIC_API_KEY``, which follows the SDK's own convention so it can be
    shared with other Anthropic tooling), falling back to a local ``.env``
    file if present. See .env.example for the full list and documentation of
    each variable.
    """

    model_config = SettingsConfigDict(
        env_prefix="IBI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Environment = "development"

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["json", "console"] = "console"

    database_url: str = Field(
        default="postgresql+psycopg://ibi:ibi@localhost:5432/ibi_dev",
        description="SQLAlchemy/psycopg connection URL.",
    )

    ai_provider: AIProviderName = "none"
    ai_model_low: str | None = None
    ai_model_standard: str | None = None
    ai_model_high: str | None = None
    ai_monthly_budget_usd: float | None = None

    market_data_provider: MarketDataProviderName = "null"
    market_data_api_key: SecretStr | None = None

    anthropic_api_key: SecretStr | None = Field(default=None, alias="ANTHROPIC_API_KEY")

    @field_validator("ai_monthly_budget_usd")
    @classmethod
    def _budget_must_be_non_negative(cls, value: float | None) -> float | None:
        if value is not None and value < 0:
            raise ValueError("ai_monthly_budget_usd must be >= 0")
        return value

    def require_anthropic_api_key(self) -> SecretStr:
        """Fail loudly, at the call site that needs it, if the key is absent.

        Deliberately not called at import time: most of the codebase (and all
        deterministic financial computation) must be usable with
        ``IBI_AI_PROVIDER=none`` and no key configured at all.
        """
        if self.ai_provider != "claude":
            raise RuntimeError(
                "ANTHROPIC_API_KEY was requested but IBI_AI_PROVIDER is "
                f"'{self.ai_provider}', not 'claude'."
            )
        if self.anthropic_api_key is None:
            raise RuntimeError(
                "IBI_AI_PROVIDER=claude but ANTHROPIC_API_KEY is not set."
            )
        return self.anthropic_api_key


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings singleton.

    Cached so validation happens once; tests that need different
    configuration should call ``get_settings.cache_clear()`` after
    monkeypatching the environment, rather than constructing ``Settings()``
    ad hoc around the codebase.
    """
    return Settings()
