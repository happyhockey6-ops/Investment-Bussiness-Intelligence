# Architecture

## Shape: modular monolith

One deployable Python package (`ibi`), organized into subpackages that are
strict domain boundaries. Each subpackage may depend on `ibi.core` and
`ibi.providers`, and may depend on other domain subpackages' *interfaces*,
but domain logic does not leak across boundaries via shared mutable state.
This is deliberately not microservices yet — see DECISIONS.md for when that
might change.

```
src/ibi/
  core/                 epistemics, evidence, uncertainty types, errors — no I/O
  config.py             all environment configuration (single entry point)
  logging.py            structured logging
  providers/
    ai/                 AIProvider interface + Claude/Local/Null implementations + router
    market_data/        MarketDataProvider interface + Null implementation + router
  db/                   SQLAlchemy models + session/engine factories
  data_engine/          source ingestion boundary (SourceConnector; implemented: sec_edgar)
  entity_engine/        entity identity/resolution boundary
  financial_engine/     deterministic financial metrics (implemented: a small real slice)
  market_engine/        price/volume-derived signal boundary
  macro_engine/         macro indicator boundary
  event_engine/         discrete dated occurrences
  knowledge_graph/      entity relationship boundary
  valuation/            multiple valuation approaches, applicability-checked
  scoring/              dynamic, context-dependent weighting
  signal_engine/        opportunity surfacing boundary
  ai_research_engine/   AI-assisted research boundary (built on providers.ai)
  thesis_engine/        bear/base/bull scenario modeling
  red_team/             contrarian analysis / thesis falsification
  backtesting_engine/   point-in-time data access, bias prevention
  learning_engine/      prediction tracking, calibration, post-mortems
  alert_engine/         alerting
  decision_engine/      decision states (not BUY/SELL)
  dashboard/            read-only presentation boundary
```

Every domain subpackage except `financial_engine`, the provider
implementations, and `data_engine.sec_edgar` (Phase 1's first real source
connector — see docs/data_architecture.md) currently contains **interfaces
and data contracts only** — `interfaces.py` with docstrings explaining
scope and an explicit "not implemented in Phase 0" note. This is
intentional: Phase 0's job is to make the boundaries and contracts right
before filling them in; Phase 1 fills in exactly one of them, deliberately,
as a proof of the whole foundation working end to end.

## The three-layer separation

1. **Source data** (`data_engine`, `db.models.source_document`,
   `db.models.observation`) — immutable once stored. A correction is a new,
   versioned record, never an edit.
2. **Deterministic computation** (`financial_engine`, future `market_engine`
   signals) — pure functions of typed inputs. No AI call, no network
   access, no wall-clock dependency inside a calculation.
   `ibi.core.errors.DeterminismViolation` exists for code that needs to
   assert this at runtime.
3. **AI interpretation** (`ai_research_engine`, future `red_team`/thesis
   synthesis) — built on `providers.ai.AIProvider`. Every output is labeled
   `INFERENCE`, `PREDICTION`, or `SPECULATION` (see
   `ibi.core.epistemics.EpistemicLabel`) — never `FACT` or `CALCULATION`.

`ibi.core.epistemics.Evidence` enforces part of this at the type level: it
raises if you try to construct a `FACT`-labeled claim whose provenance
source tier is `AI_GENERATED` or `UNVERIFIED`.

## Provider independence

`ibi.providers.ai.base.AIProvider` and
`ibi.providers.market_data.base.MarketDataProvider` are the only interfaces
domain engines are allowed to depend on for AI/market-data capability. No
domain module imports an SDK or vendor client directly. Concrete
implementations (`ClaudeProvider`, `LocalAIProvider`, `NullAIProvider`,
`NullMarketDataProvider`) are wired in by `ibi.config.Settings` via
`providers.ai.router.build_ai_provider` /
`providers.market_data.router.build_market_data_provider`. No production
market-data vendor is selected in Phase 0.

## Data integrity

- Raw source data is immutable; corrections are new versioned records.
- Every numeric/categorical result that might not be knowable is typed as
  `T | Unknown | InsufficientEvidence | ConflictingEvidence`
  (`ibi.core.types`), never silently defaulted or fabricated.
- `financial_engine` functions return `InsufficientEvidence` rather than
  raising or returning a nonsensical number for undefined arithmetic (e.g.
  division by zero in a margin calculation).

See [docs/data_architecture.md](docs/data_architecture.md) and
[docs/epistemic_model.md](docs/epistemic_model.md) for detail.

## Decision states, not BUY/SELL

`ibi.decision_engine.interfaces.DecisionState` is: `STRONG_OPPORTUNITY`,
`OPPORTUNITY`, `WATCH`, `NEUTRAL`, `AVOID`, `INSUFFICIENT_EVIDENCE`,
`CONFLICTING_EVIDENCE`. There is no automatic execution anywhere in this
project's scope.

## Backtesting: point-in-time by construction

`ibi.backtesting_engine.interfaces.PointInTimeDataset` is the only
sanctioned read path for simulated historical decisions. Its query type
carries an explicit `as_of` timestamp, and its contract requires that no
record with a later *availability* timestamp ever be returned — this is
what prevents look-ahead bias, survivorship bias, and general
future-information contamination once real backtesting is implemented.
Availability is deliberately not `retrieval_date` (when the system fetched
a record) — see `ibi.data_engine.sec_edgar`'s `known_available_at` for the
concrete pattern established in Phase 1, and DECISIONS.md for why.

## What Phase 0 deliberately does not build

Per the Phase 0 charter: no UI, no autonomous agents, no full knowledge
graph, no full financial/valuation/scoring engines, no live research
pipeline, no brokerage integration, no trade execution, no production
market-data vendor selection. Each of those has a domain boundary ready to
receive the implementation when its phase arrives.

## Phase 1: what changed

`data_engine.sec_edgar` is a real, tested ingestion source — the first
domain package to move beyond interfaces. Everything else above still
holds: no financial computation on the ingested data, no market data, no
AI research, no UI, no vendor selection beyond SEC itself, and the fixed
two-company universe is not to be expanded without a deliberate decision
(see DECISIONS.md). One new table (`filings`) and five new columns on
`financial_data` were added, both additively — the Phase 0 migration was
not modified.
