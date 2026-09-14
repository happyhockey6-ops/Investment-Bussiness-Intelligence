"""A no-op `AIProvider` used when `IBI_AI_PROVIDER=none` and in tests.

This is the default. It lets the entire codebase — including every domain
engine that will eventually call an `AIProvider` — be imported, tested, and
exercised with zero AI spend and zero network access, and it makes
"deterministic-only mode" a first-class, explicitly supported configuration
rather than an accident of missing credentials.
"""

from __future__ import annotations

from ibi.core.errors import ProviderError
from ibi.providers.ai.base import AIProvider, AIRequest, AIResponse


class NullAIProvider(AIProvider):
    """Always refuses to complete a request, loudly and explicitly."""

    @property
    def name(self) -> str:
        return "none"

    def complete(self, request: AIRequest) -> AIResponse:
        raise ProviderError(
            "AI provider is configured as 'none' (deterministic-only mode). "
            "Set IBI_AI_PROVIDER to 'claude' or 'local' to enable AI calls."
        )
