"""AI provider abstraction and model-tier routing.

See `base.AIProvider` for the interface every implementation must satisfy,
and `router.ModelTier` for how the platform is meant to think about
capability/cost trade-offs once real routing is implemented.
"""

from ibi.providers.ai.base import AIProvider, AIRequest, AIResponse

__all__ = ["AIProvider", "AIRequest", "AIResponse"]
