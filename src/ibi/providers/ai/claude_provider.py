"""Claude implementation of `AIProvider`.

Deliberately not wired to the Anthropic SDK in Phase 0 — this class exists
to prove the interface is implementable and to give later phases a concrete
place to add the real HTTP call, retry policy, and token accounting. No
domain engine should import this module directly; they should depend on
`ibi.providers.ai.base.AIProvider` and receive whichever implementation is
configured (see `ibi.config.Settings.ai_provider`).
"""

from __future__ import annotations

from ibi.core.errors import ConfigurationError
from ibi.providers.ai.base import AIProvider, AIRequest, AIResponse


class ClaudeProvider(AIProvider):
    """Calls the Anthropic API. Requires `ANTHROPIC_API_KEY` to be configured."""

    def __init__(self, api_key: str, default_model_by_tier: dict[str, str] | None = None) -> None:
        if not api_key:
            raise ConfigurationError("ClaudeProvider requires a non-empty api_key.")
        self._api_key = api_key
        self._default_model_by_tier = default_model_by_tier or {}

    @property
    def name(self) -> str:
        return "claude"

    def complete(self, request: AIRequest) -> AIResponse:
        raise NotImplementedError(
            "ClaudeProvider.complete is not implemented in Phase 0. "
            "Phase 0 establishes provider abstractions only; wiring the real "
            "Anthropic API call is Phase 1+ work, gated on IBI_AI_PROVIDER=claude."
        )
