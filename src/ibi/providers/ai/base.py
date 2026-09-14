"""The `AIProvider` interface.

Architectural rule this interface exists to enforce: the platform is not
architecturally dependent on any one AI vendor. Every engine that wants an
AI capability (research synthesis, classification, red-teaming, natural
language explanation) depends on this interface, never on a vendor SDK
directly. Swapping `ClaudeProvider` for `LocalAIProvider` or a future
`OtherAIProvider` must not require changes in any domain engine.

Equally important, this interface has no method that returns a financial
*calculation* — an `AIProvider` can classify, summarize, compare, and
explain, but computing a number like EBITDA or ROIC is `financial_engine`'s
job. If a future engine finds itself asking an `AIProvider` to "calculate"
something, that is an architecture violation (see DECISIONS.md).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum


class ModelTier(StrEnum):
    """Capability/cost tier a request is routed to.

    See `router.py` for the (Phase 0: config-only) tier -> model mapping.
    Callers ask for a tier, not a model name, so the mapping can change
    without touching call sites.
    """

    LOW = "low"  # normalization, classification, screening, simple extraction
    STANDARD = "standard"  # research, synthesis, explanation
    HIGH = "high"  # deep research, red-team, contrarian analysis, complex synthesis


@dataclass(frozen=True, slots=True)
class AIRequest:
    """A single request to an AI provider.

    ``prompt`` is assumed to already have been assembled by the caller
    (e.g. `ai_research_engine`) including any retrieved evidence. This
    interface does not do prompt construction or retrieval.
    """

    prompt: str
    tier: ModelTier
    max_output_tokens: int | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    """Free-form context for logging (e.g. {"operation": "thesis.summarize",
    "entity_id": "..."}) — never used for authorization or business logic."""


@dataclass(frozen=True, slots=True)
class AIResponse:
    """A provider's response, plus enough metadata to log and audit it.

    ``text`` is untrusted content, even though this system produced it: it
    must be labeled with an appropriate `ibi.core.epistemics.EpistemicLabel`
    (typically INFERENCE, PREDICTION, or SPECULATION) before being stored,
    never FACT or CALCULATION.
    """

    text: str
    provider_name: str
    model: str
    tier: ModelTier
    input_tokens: int | None = None
    output_tokens: int | None = None


class AIProvider(ABC):
    """Contract every AI backend (Claude, a local model, another vendor) must implement."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable provider identifier used in logs and stored evidence provenance."""

    @abstractmethod
    def complete(self, request: AIRequest) -> AIResponse:
        """Send a request and return the response.

        Implementations are responsible for their own error handling,
        raising `ibi.core.errors.ProviderError` on failure rather than
        leaking a vendor-specific exception type to callers.
        """
