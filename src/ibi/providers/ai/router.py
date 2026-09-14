"""Model-tier routing and (future) budget control.

Phase 0 scope: establish the configuration shape a router needs, and a
factory that turns `ibi.config.Settings` into an `AIProvider`, without
implementing per-request budget enforcement, retries, or fallback between
providers. Those are Phase 1+ concerns once there are real call sites to
route.
"""

from __future__ import annotations

from ibi.config import Settings
from ibi.core.errors import ConfigurationError
from ibi.providers.ai.base import AIProvider
from ibi.providers.ai.claude_provider import ClaudeProvider
from ibi.providers.ai.null_provider import NullAIProvider


def build_ai_provider(settings: Settings) -> AIProvider:
    """Construct the configured `AIProvider`. The only place that should
    know all three concrete provider classes exist."""
    if settings.ai_provider == "none":
        return NullAIProvider()
    if settings.ai_provider == "claude":
        api_key = settings.require_anthropic_api_key()
        return ClaudeProvider(
            api_key=api_key.get_secret_value(),
            default_model_by_tier={
                "low": settings.ai_model_low or "",
                "standard": settings.ai_model_standard or "",
                "high": settings.ai_model_high or "",
            },
        )
    if settings.ai_provider == "local":
        raise ConfigurationError(
            "IBI_AI_PROVIDER=local requires a local model endpoint, which is "
            "not yet a configured setting — add one before enabling this provider."
        )
    raise ConfigurationError(f"Unknown ai_provider: {settings.ai_provider!r}")


class MonthlyBudgetTracker:
    """Placeholder for AI spend tracking.

    Phase 0 intentionally does not implement enforcement (no persistence of
    spend-to-date exists yet). This class exists so `router.build_ai_provider`
    and callers have a documented extension point rather than needing a
    later redesign of the call signature.
    """

    def __init__(self, monthly_budget_usd: float | None) -> None:
        self.monthly_budget_usd = monthly_budget_usd

    def check(self) -> None:
        """No-op in Phase 0. Will raise `ProviderError` once spend tracking exists."""
        return None
