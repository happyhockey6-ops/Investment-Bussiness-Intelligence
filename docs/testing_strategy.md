# Testing Strategy

## Layout

```
tests/
  conftest.py           shared fixtures (settings-cache isolation)
  unit/                 no I/O, no external services, run in ~seconds
  integration/           may use a database (in-memory/temp, or real if reachable)
```

Run everything: `pytest`. Run only what needs no external service at all:
`pytest -m "not integration"`.

## What's covered in Phase 0

- **Configuration** (`test_config.py`) — defaults are deterministic-only;
  `require_anthropic_api_key` fails correctly in both missing-provider and
  missing-key cases; negative AI budget is rejected; settings caching
  behaves as documented.
- **Epistemic model** (`test_epistemics.py`) — the core safety property:
  a `FACT`-labeled claim from an AI-generated or unverified source must be
  rejected at construction time; confidence must be in `[0, 1]`.
- **Financial metrics** (`test_financial_metrics.py`) — each implemented
  metric (`revenue_growth`, `gross_margin`, `free_cash_flow`, `roic`) is
  checked for the happy path, the undefined-arithmetic path (must return
  `InsufficientEvidence`, never raise or fabricate), and determinism
  (same inputs -> same output across repeated calls).
- **Provider abstraction** (`test_provider_interfaces.py`) — the null
  providers satisfy their ABCs; the routers return the right concrete type
  for each configuration and fail correctly for unimplemented
  configurations (`local`, `other`).
- **Uncertainty types** (`test_types.py`) — `Unknown` /
  `InsufficientEvidence` / `ConflictingEvidence` are distinguishable from
  real values via `is_uncertain`.
- **Thesis scenarios** (`test_thesis_scenarios.py`) — a `Thesis` structurally
  requires exactly one bear, one base, one bull scenario.
- **Database schema** (`test_db_models_smoke.py`) — every model in
  `ibi.db.models` is creatable and can round-trip a row, checked against an
  in-memory SQLite engine (no external service required).
- **Migration** (`test_alembic_migration.py`) — `alembic upgrade head`
  actually runs against a temporary SQLite database and produces every
  table `Base.metadata` declares.
- **Real database connectivity** (`test_postgres_connection.py`, marked
  `integration`) — attempts a real connection to `IBI_DATABASE_URL` with a
  short timeout and self-skips if none is reachable, so the suite stays
  fast and green with no database running, but exercises the real thing
  automatically once one exists.

## What's deliberately not covered

Every domain package beyond `financial_engine`'s implemented slice is an
interface with no behavior (`NotImplementedError` or `ABC`). There is
nothing meaningful to test yet in `market_engine`, `signal_engine`,
`ai_research_engine`, `decision_engine`'s actual decision logic, etc. —
writing tests against `NotImplementedError` stubs would be tests that
"merely pass without validating behavior", which the Phase 0 charter
explicitly rules out. As each engine gains real logic, it should gain
tests in the same commit, following the patterns above:

- Deterministic code (anything in `financial_engine`/`market_engine`) needs
  a happy-path test, an undefined-input test asserting `Uncertain` (not an
  exception or fabricated value), and ideally a determinism test.
- Anything touching `AIProvider` should be tested against a fake/null
  provider, never a real API call, so the suite stays fast, free, and
  deterministic.
- Anything touching the database should default to the in-memory/temporary
  SQLite pattern used here, with real-PostgreSQL checks marked
  `integration` and self-skipping when unavailable.

## Configuration test isolation

`ibi.config.get_settings` is an `lru_cache`d singleton by design (see its
docstring). The `_clear_settings_cache` fixture in `conftest.py` is
`autouse=True` specifically so tests that monkeypatch environment variables
can't leak cached `Settings` into other tests — this was a real early
failure mode found while writing the Phase 0 suite, not a hypothetical one.
