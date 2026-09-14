# Investment Intelligence Platform

A platform for improving investment decision quality through reliable data,
deterministic computation, traceable evidence, deep research, scenario
analysis, contrarian analysis, and learning from prediction errors.

**This is not a trading bot.** There is no brokerage integration, no
autonomous trading, and no automatic execution anywhere in this project's
scope. See [DECISIONS.md](DECISIONS.md) for why.

## Status: Phase 0 (Foundation)

This repository currently contains the **foundation** of the platform, not
the platform itself: domain boundaries, provider abstractions, the
evidence/epistemic model, a database schema and migration system, and a
tested slice of deterministic financial computation. Most domain engines
are interfaces with no behavior yet — see [ARCHITECTURE.md](ARCHITECTURE.md)
for what exists and what is deliberately deferred.

## Core principle

Source data, deterministic computation, and AI interpretation are different
kinds of things, and the codebase keeps them structurally separate:

- **Source data** — immutable, versioned, provenance-tagged records of what
  was actually observed or reported.
- **Deterministic computation** (`ibi.financial_engine`, future
  `ibi.market_engine` signals) — pure functions of typed inputs, no AI
  involvement, reproducible forever.
- **AI interpretation** (`ibi.ai_research_engine`, future red-team/thesis
  synthesis) — classification, synthesis, hypothesis generation, and
  explanation, always labeled `INFERENCE`/`PREDICTION`/`SPECULATION`, never
  a source of financial fact.

When evidence is insufficient, the correct answer is "I don't know" —
see `ibi.core.types.Unknown` / `InsufficientEvidence` / `ConflictingEvidence`.

## Getting started

See [DEVELOPMENT.md](DEVELOPMENT.md) for environment setup, running tests,
and running migrations.

## Documentation map

| Doc | Covers |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | System architecture, domain boundaries, what's implemented vs. deferred |
| [DECISIONS.md](DECISIONS.md) | Why the architecture is shaped this way; open questions |
| [SECURITY.md](SECURITY.md) | Secrets handling, trust boundaries, known limitations |
| [DEVELOPMENT.md](DEVELOPMENT.md) | Environment setup, running tests/migrations |
| [docs/epistemic_model.md](docs/epistemic_model.md) | The evidence/epistemic labeling system in depth |
| [docs/data_architecture.md](docs/data_architecture.md) | Data integrity, immutability, point-in-time design |
| [docs/provider_architecture.md](docs/provider_architecture.md) | AI/market-data provider abstraction |
| [docs/testing_strategy.md](docs/testing_strategy.md) | What's tested, how, and why |
