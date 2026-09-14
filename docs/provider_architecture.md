# Provider Architecture

## The problem this solves

Two categories of external dependency — AI models and market/reference
data — are both (a) likely to change vendors over the platform's life and
(b) dangerous to let leak into domain logic, because domain logic written
against a specific vendor's quirks becomes expensive to migrate later. Both
get the same treatment: an abstract interface domain code depends on, one
or more concrete implementations, and a config-driven factory that decides
which implementation is active.

## AI provider (`ibi.providers.ai`)

- `base.AIProvider` — the interface. One method, `complete(AIRequest) ->
  AIResponse`. Nothing about the interface exposes any vendor-specific
  concept (no "system prompt caching", no vendor-specific parameters).
- `base.ModelTier` — `LOW` / `STANDARD` / `HIGH`. Callers request a
  capability tier, not a model name, so routing decisions live in one
  place (`router.py`) instead of being duplicated at every call site.
- `null_provider.NullAIProvider` — the default (`IBI_AI_PROVIDER=none`).
  Raises `ProviderError` rather than silently returning empty output, so
  "AI is disabled" is loud and unambiguous.
- `claude_provider.ClaudeProvider` / `local_provider.LocalAIProvider` —
  structurally complete, behaviorally unimplemented (`complete` raises
  `NotImplementedError`). They exist to prove the interface is
  implementable by more than one vendor shape, and to give Phase 1+ a
  concrete place to add the real HTTP call.
- `router.build_ai_provider(settings)` — the only function that imports all
  three concrete classes. Domain code should never import
  `ClaudeProvider`/`LocalAIProvider`/`NullAIProvider` directly; it should
  receive an `AIProvider` (e.g. via dependency injection at the
  application's composition root, not yet built in Phase 0).
- `router.MonthlyBudgetTracker` — a documented no-op. The configuration
  surface (`IBI_AI_MONTHLY_BUDGET_USD`) exists; enforcement does not yet,
  because there are no real call sites to enforce against.

## Market data provider (`ibi.providers.market_data`)

Same shape, smaller surface (`get_price_history` only — extend the
request/response types as real usage in `market_engine` demands, not ahead
of it):

- `base.MarketDataProvider` / `PriceQuery` / `PriceBar`.
- `null_provider.NullMarketDataProvider` — the only implementation in
  Phase 0. Returns `ibi.core.types.Unknown`, never fabricated data.
- `router.build_market_data_provider(settings)` — raises
  `ConfigurationError` for `IBI_MARKET_DATA_PROVIDER=other`, since no second
  implementation exists yet. This is deliberate: Phase 0 does not select a
  production market-data vendor.

## Model/cost routing (future)

The three-tier (`LOW`/`STANDARD`/`HIGH`) split maps to the platform's cost
model: normalization/classification/screening work should never use the
same model as deep research or red-team analysis. Phase 0 defines the tier
vocabulary and the settings fields (`IBI_AI_MODEL_LOW/STANDARD/HIGH`) but
does not implement per-request routing logic, retry/fallback between
providers, or budget enforcement — those require real call sites to design
against.

## Adding a new provider

1. Implement the relevant `ABC` (`AIProvider` or `MarketDataProvider`).
2. Add a `Literal` value to the corresponding `*ProviderName` type in
   `ibi.config`.
3. Wire it into the corresponding `router.build_*_provider` function.
4. Do not change the interface itself to accommodate the new provider's
   quirks — if the interface genuinely can't express something a new
   provider needs, that's a signal the interface needs deliberate revision
   (see DECISIONS.md), not a one-off special case.
