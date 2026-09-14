# Data Architecture

## Immutability

Raw ingested records (`db.models.source_document.SourceDocumentRecord`,
`db.models.observation.ObservationRecord`) are never updated in place. Every
model in `db.models` inherits `TimestampMixin`, which sets `created_at`
once and documents that a correction is a new row, not an edit — see the
docstring on `TimestampMixin` in `ibi/db/base.py`. This applies throughout:
`OutcomeRecord` (learning_engine) and `EventRecord` (event_engine) are
explicitly append-only for the same reason — rewriting history would break
the ability to reconstruct "what did we believe, and when."

## Versioning

`db.models.financial.FinancialDataPointRecord.calculation_version` ties a
stored `CALCULATION`-labeled value to the exact `financial_engine` formula
version that produced it. When a formula changes (a bug fix, a definitional
change in how a metric is computed), historical rows keep their original
`calculation_version` rather than being silently reinterpreted under the
new formula — reproducibility of a past analysis depends on this.

## Point-in-time design

See [epistemic_model.md](epistemic_model.md#provenance) for the
`retrieval_date` vs. `observation_date`/`publication_date` distinction.
`ibi.backtesting_engine.interfaces.PointInTimeDataset` is the architectural
enforcement point: any code path that simulates a historical decision must
read through this interface (once implemented) rather than querying `db`
directly, so it structurally cannot see a `retrieval_date` later than its
simulated `as_of` timestamp.

This is what prevents, by construction rather than by discipline alone:

- **Look-ahead bias** — using a restated/revised figure as if it were the
  originally reported one.
- **Survivorship bias** — `PointInTimeDataset.entities_as_of` must include
  entities that later failed or were delisted, and exclude entities not yet
  tracked at that point in time.
- **General future-information contamination** — any fact, event, or piece
  of research retrieved after the simulated timestamp.

## Generic vs. structured storage

`db.models.observation.ObservationRecord` is a deliberate escape hatch: a
generic `{key, value}` fact store for anything that doesn't yet warrant a
dedicated, strongly-typed table. High-volume, well-understood data
(financial metrics, market bars) gets its own table
(`financial_data`, `market_data`) so it can be indexed and typed properly;
everything else goes through `observations` so `data_engine` is never
blocked on a schema migration just to record a new fact type. As a fact
type matures and proves it needs structure, it should graduate to its own
table.

## Reproducibility

A `CALCULATION` row must be re-derivable from its recorded inputs and
`calculation_version` alone — this is why `financial_engine` functions are
required to be pure (see ARCHITECTURE.md's determinism section) and why
`MetricCalculation.metric_id`/`calculation_version` exist as first-class
concepts rather than being inferred from context.

## Exact table inventory

16 tables are defined, identically, across `src/ibi/db/models/*.py` and
`alembic/versions/50445f5a9e59_initial_schema.py` (verified by
`tests/integration/test_alembic_migration.py`, which asserts the migration
creates every table `Base.metadata` declares — a mismatch there fails the
test):

`entities`, `source_documents`, `observations`, `financial_data`,
`market_data`, `events`, `claims`, `evidence`, `research`, `theses`,
`scenarios`, `predictions`, `outcomes`, `alerts`, `decisions`,
`provider_calls`.

**Why 16, not 14:** the original schema requirement named 14 conceptual
record categories (entities, source documents, observations, financial
data, market data, events, claims, evidence, research, theses,
predictions, alerts, decisions, model/provider metadata). Two of those
categories are properly normalized into a parent + child table rather than
one table each: a **thesis** is one row plus exactly three **scenario**
rows (bear/base/bull — see `ibi.thesis_engine.interfaces.Thesis`), and a
**prediction** is one row plus zero-or-one **outcome** row recorded when it
resolves. Cramming either into a single table would mean either a fixed set
of bear/base/bull columns (rejected — `ScenarioRecord` is one row per
scenario so a future fourth scenario kind isn't a schema change) or nullable
outcome columns bolted onto `predictions` (rejected — outcomes are written
at a different time, by different code, and should not fabricate an
empty-vs-unresolved distinction with nullable columns). 14 named categories
→ 16 tables is a faithful, minimal normalization of the requirement, not
scope creep.

## PostgreSQL-specific review

Audited `src/ibi/db/models/*.py` and the migration for PostgreSQL-specific
risk. No live PostgreSQL instance was available to validate this against —
see the procedure in DEVELOPMENT.md. Findings:

- **JSON vs JSONB** — fixed. All 6 JSON-typed columns (`observations.value`,
  `theses.evidence_ids`, and `scenarios.{assumptions,catalysts,risks,unknowns,invalidation_conditions}`)
  use `ibi.db.base.portable_json()`, which renders as `JSONB` on PostgreSQL
  (indexable, binary storage — the recommended type for new PostgreSQL
  JSON columns) and falls back to plain `JSON` on every other dialect
  (SQLite, for tests). The migration uses the same
  `sa.JSON().with_variant(postgresql.JSONB(), "postgresql")` construction
  so both stay in sync.
- **UUID** — not used anywhere. `entity_id` and other identifiers are
  plain `String`, not `UUID`/`uuid.uuid4()`. This sidesteps
  PostgreSQL-vs-SQLite UUID representation differences entirely; revisit
  only if `entity_engine` later needs globally-unique, non-guessable ids.
- **Timestamps** — every timestamp column uses `DateTime(timezone=True)`,
  which is `timestamptz` on PostgreSQL (the correct choice — avoids the
  classic "naive timestamp in an unknown timezone" bug). SQLite has no
  native tz-aware storage, so this is exercised only structurally in the
  SQLite-based tests, not for actual UTC-correctness — another reason the
  real-PostgreSQL validation pass matters.
- **Enums** — no PostgreSQL native `ENUM` type is used; enum-like fields
  (`epistemic_label`, `source_tier`, `kind`, `state`, `severity`, ...) are
  plain `String(N)` columns, validated at the application layer (Python
  `StrEnum`s in `ibi.core.epistemics` / domain `interfaces.py` modules).
  **Deliberate**: PostgreSQL native enums require an `ALTER TYPE ... ADD
  VALUE` migration (with its own transactional restrictions) every time a
  label vocabulary grows, which is expected to happen often in early
  phases (e.g. adding a `DecisionState`). Plain strings keep that change to
  an application-layer, zero-migration change. **Trade-off, documented as
  a known limitation**: the database itself does not reject an invalid
  label value — see "Constraints" below.
- **Constraints** — no `CHECK` constraints exist (e.g. nothing stops a raw
  SQL write from storing `confidence = 5.0` or
  `epistemic_label = 'not_a_real_label'` directly in the database).
  Validation currently exists only at the application layer
  (`ibi.core.epistemics.Evidence.__post_init__`,
  `ibi.thesis_engine.interfaces.Scenario.__post_init__`, etc.) — it is
  bypassed by anything that writes through raw SQL or a future
  non-Python client. **Known, deliberate gap**: adding `CHECK` constraints
  is schema hardening appropriate to Phase 1+, once the label vocabularies
  are considered stable; adding them now against vocabularies still likely
  to change would itself require migrations to loosen. Not fixed in this
  pass — flagged for follow-up rather than added speculatively.
- **Indexes** — only primary keys are indexed. Foreign-key columns (e.g.
  every `entity_id` column) have **no** explicit index; PostgreSQL, unlike
  some other databases, does not automatically index foreign-key columns.
  At zero rows of real data there is no query pattern yet to index against
  correctly. **Known, deliberate gap** — add indexes once `entity_engine`
  and `data_engine` produce real query patterns to measure, not
  speculatively now.
- **Foreign keys** — present and consistent between models and migration
  (verified column-by-column during this audit); all reference the correct
  parent table and column.
- **Nullable/non-nullable** — verified column-by-column between
  `src/ibi/db/models/*.py` and the migration during this audit; every
  column's nullability matches exactly between the two.
- **Uniqueness constraints** — none defined, and none are currently
  needed: every table is either a natural log (`observations`, `events`,
  `market_data`, `provider_calls` — multiple rows per entity are expected)
  or already has a sufficient primary key (`entities.entity_id`).

## What's not yet built

`data_engine.interfaces.SourceConnector` defines the contract for pulling
data from an external source into `RawRecord`s, but no concrete connector
exists yet — Phase 0 does not ingest real data. The schema and immutability
rules above are validated by `tests/integration/test_db_models_smoke.py`
and `test_alembic_migration.py` using synthetic rows, not real ingested
data.
