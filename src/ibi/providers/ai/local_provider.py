"""Local/offline implementation of `AIProvider`.

Intended future home for a locally-hosted model (e.g. via Ollama or a
self-hosted inference server), so the platform can run its AI-interpretation
layer without any external vendor when that is desired (cost control,
data-residency, or offline development). Not implemented in Phase 0.
"""

from __future__ import annotations

from ibi.providers.ai.base import AIProvider, AIRequest, AIResponse


class LocalAIProvider(AIProvider):
    """Placeholder for a self-hosted/local model backend."""

    def __init__(self, endpoint: str) -> None:
        self._endpoint = endpoint

    @property
    def name(self) -> str:
        return "local"

    def complete(self, request: AIRequest) -> AIResponse:
        raise NotImplementedError(
            "LocalAIProvider.complete is not implemented in Phase 0."
        )
