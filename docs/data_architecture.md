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

Every stored calculation is tied to the exact formula version
(`calculation_version`) *and* the exact input-selection rules
(`selection_policy_version`) that produced it. When either changes (a bug
fix, a definitional change, a new tag mapping), historical results keep
their original versions rather than being silently reinterpreted —
reproducibility of a past analysis depends on this.

**Phase 2B update:** calculations are stored in `metric_results` (see
"Phase 2B: calculation results" below), not in `financial_data`.
`financial_data.calculation_version` predates that decision; it is left in
place, unused (always NULL), because dropping it would be a destructive
migration. `financial_data` holds facts only.

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
versions alone — this is why `financial_engine` functions are required to
be pure (see ARCHITECTURE.md's determinism section) and why
`MetricCalculation.metric_id`/`calculation_version` exist as first-class
concepts rather than being inferred from context. Phase 2B enforces this
mechanically: every build re-derives the previous generation from its own
input snapshot and refuses to write if the stored rows are not reproduced
exactly.

## Exact table inventory

**Phase 2B update:** 22 tables — the 17 below plus `metric_generations`,
`metric_results`, `metric_result_inputs`, `metric_version_activations`
and `fact_quarantine` (migration `e7f3a2c91b40`). A live inspection shows
23 including `alembic_version`.

17 tables are defined, identically, across `src/ibi/db/models/*.py` and the
Alembic migration chain (verified by `tests/integration/test_alembic_migration.py`,
which asserts the migrations create every table `Base.metadata` declares —
a mismatch there fails the test):

`entities`, `source_documents`, `observations`, `financial_data`,
`market_data`, `events`, `claims`, `evidence`, `research`, `theses`,
`scenarios`, `predictions`, `outcomes`, `alerts`, `decisions`,
`provider_calls` (all from the Phase 0 migration, `50445f5a9e59`), plus
`filings` (added in Phase 1, migration `50dc0583f9c1`) — the first table
introduced after Phase 0's baseline, holding SEC (and, in future,
provider-neutral) filing metadata; see "SEC EDGAR ingestion" below.

(A live database inspection will show 18 tables, not 17 — the extra one is
`alembic_version`, Alembic's own bookkeeping table, which is not part of
`Base.metadata` and is deliberately excluded from this application-table
count.)

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

Audited `src/ibi/db/models/*.py` and the migrations for PostgreSQL-specific
risk. The full migration chain — Phase 0's baseline (`50445f5a9e59`) and
both Phase 1 migrations (`50dc0583f9c1`, `a950c85c80b9`) — has been run
against a real PostgreSQL 18.6 instance, confirmed at head with zero
`alembic check` drift, including direct confirmation that the two partial
unique indexes on `financial_data` exist by name on real PostgreSQL, not
just SQLite. See DECISIONS.md for the full result. Findings:

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
- **Uniqueness constraints** — as of Phase 0, none were needed (every table
  was either a natural log or already had a sufficient primary key). Phase 1
  added two: `filings.accession_number` (plain `UNIQUE` — SEC accession
  numbers are globally unique by construction, safe immediately) and the
  two partial unique indexes on `financial_data` implementing the XBRL fact
  identity model — see "SEC EDGAR ingestion" below.

## SEC EDGAR ingestion (Phase 1)

`ibi.data_engine.sec_edgar` is the first real `SourceConnector`
implementation. Its identity model, point-in-time rule, and provenance
design went through several review passes — see DECISIONS.md, "Phase 1:
SEC EDGAR ingestion," for the full history. Summary:

- **Identity**: a stored XBRL fact is uniquely identified by
  `(entity_id, metric_id, unit, start_date, end_date, accession_number)`,
  enforced by two partial unique indexes on `financial_data`
  (`uq_financial_data_duration_fact` where `start_date IS NOT NULL`,
  `uq_financial_data_instant_fact` where `start_date IS NULL` — PostgreSQL
  treats `NULL <> NULL`, so one plain constraint can't cover both fact
  shapes). Empirically validated against ~58,000 real fact entries from two
  companies (Apple, Microsoft) before either index was created — zero
  collisions found; the flawed key without `unit`/`start_date` collided on
  thousands of real facts (e.g. an annual and a Q4-only duration reported
  under the same tag/end-date/accession).
- **Point-in-time**: `known_available_at` (+ `availability_precision`,
  `"acceptance_timestamp"` or `"day_conservative"`) implements the approved
  hybrid rule — SEC's `acceptanceDateTime` when it falls within EDGAR's
  documented operating hours on an SEC business day, otherwise the next
  SEC/federal business day after `filing_date`. `retrieval_date` never
  participates in this computation. See `ibi.data_engine.sec_edgar.availability`.
- **Provenance**: two distinct edges, never conflated — `accession_number`
  (+ `filings` table) answers "what filing produced this," while
  `source_document_id` answers "which raw API snapshot did we read it
  from." Phase 1 does not fetch or store the original filing's HTML/XML
  bytes — only SEC's own filing metadata (`submissions.json`) and
  aggregated XBRL facts (`companyfacts.json`).
- **Fixed universe**: exactly two companies (Apple, CIK 320193; Microsoft,
  CIK 789019) — see `ibi.data_engine.sec_edgar.fixed_universe`. Changing
  this list is a deliberate decision, not a routine edit.
- **Raw snapshot archival**: every fetched JSON response is written
  verbatim to `IBI_RAW_DATA_DIR` (default `data/raw/`, gitignored — runtime
  data, not source code) and referenced from `source_documents.content_ref`.

## Phase 2B: calculation results

**Where things live.** `financial_data` holds facts only. Each build is a
**generation** (`metric_generations`): one entity, one metric, one
`(calculation_version, selection_policy_version)` pair, over one exactly
identified input snapshot. The snapshot is identified by the highest
`financial_data`, `filings` and `fact_quarantine` ids the build saw (which
select it) plus `input_set_hash` (which proves it can still be
reproduced). `policy_manifest_hash` pins the policy content the version
string meant.

**Results.** `metric_results` holds one row per (entity, metric, version
pair, period, epoch).
- *Epochs:* an epoch is a distinct `known_available_at` of a relevant fact,
  or of an 8-K item 4.02 filing. A row is written only when the resolved
  state or its provenance changes, so the latest row with
  `effective_from <= T` is exactly the answer as of T.
- *Append-only per version pair:* retroactive drift is an error, so a new
  generation only ever adds epochs after the previous one's last epoch.
  Each row is written once, by the generation that first produced it
  (`generation_id`). Unchanged history is never copied. Generation g's view
  is the pair's rows with `generation_id <= g`.
- *Status:* `value` or one of four uncertainty states. A CHECK constraint
  makes `value` NULL, and `reason_code` non-NULL, for every uncertain row.
- *Provenance:* `metric_result_inputs` links each row to every fact that
  informed it, labelled `input`, `corroborating`, `conflicting`,
  `unverified_revision`, `superseded_basis` or `check_term`.

**Selection (policy v1, `ibi.financial_engine.policy`).**
- *Single basis:* all inputs come from one eligible accession, the latest
  one reporting every input for the exact period. Figures are never
  combined across filings.
- *Eligible:* 10-K, 10-Q and their /A amendments.
- *8-K/8-K/A:* eligible only if the accession carries every evidence kind
  the policy requires, and every identity check of the metric can be
  evaluated and passes inside it. A complete set of tags alone never
  qualifies. v1 requires `xbrl_instance_restatement_classified`, which only
  the filing's own XBRL instance can provide and which is not ingested, so
  under v1 no 8-K qualifies yet. An eligible 8-K basis is annotated
  `revision_kind = recast`.
- *Other and unknown forms:* never eligible.
- *Divergence fails closed:* any later or simultaneous evidence that
  diverges, from any form, makes the result uncertain. It is never resolved
  by choosing a number.
- *Exact comparisons:* values use exact `Decimal` equality, and the identity
  checks (e.g. gross profit = revenue − cost) are evaluated inside the
  basis.
- *Non-reliance:* an 8-K item 4.02 filed after the basis covers every period
  ending before it.
- See `ibi.financial_engine.resolver` for the precedence order.

**Time axes.**
| Axis | Field |
|---|---|
| Input vintage (world time) | `financial_data.known_available_at` → `metric_results.effective_from` |
| Formula | `calculation_version` |
| Selection rules | `selection_policy_version` |
| Input snapshot | generation watermarks + `input_set_hash` |
| System time | `created_at`, application clock, on every table |

**Reads (`ibi.financial_engine.results_store`).** The reader never filters
by status, so an uncertain epoch is never skipped in favour of an older
value. Every read takes an explicit `VersionPair`. There is no default
pair, no fallback to another pair, and `require_values` rejects a series
that mixes pairs.
- **M1 `read_pinned`:** exactly what generation g answered, forever. The
  generation must belong to the given entity, metric and pair.
- **M2 `read_current`:** the latest generation for the pair. It returns
  `UnverifiedRevision(GENERATION_BEHIND_INPUTS)` instead of an answer
  whenever facts, quarantines or 4.02 filings that the generation never saw
  were already available by `as_of`. That includes a build blocked by
  retroactive drift.
- **M3 `read_at_system_time`:** what the latest generation of the pair that
  existed at system time S answered.
- **`active_versions(metric, S)`:** only *looks up* which pair was
  designated active. The caller then passes that pair explicitly.

**Build and recovery rules.**
- *Unchanged snapshot:* rebuilding writes nothing.
- *Stored inputs changed:* `InputMutationError`.
- *Stored rows not reproduced:* `ResultDriftError`.
- *Same policy version, different content:* `PolicyManifestMismatchError`.
- *Retroactive change:* inputs that would change history at or before the
  previous generation's last epoch raise `RetroactiveDriftError` and write
  nothing. This includes a fact discovered late, and a newly quarantined
  fact. The approved remedy is an explicit new selection-policy version,
  which builds a fresh history, or a separately approved recovery
  procedure. Historical results are never rewritten, and old generations
  remain readable through M1/M3.
- *Bad facts:* excluded by an append-only `fact_quarantine` row. A fact is
  never edited or deleted.

## What's not yet built

Every other domain package's `SourceConnector`-equivalent remains
interfaces only — `sec_edgar` is the first and only concrete ingestion
source. Phase 2B computes two single-period metrics (`ibi:gross_margin`,
`ibi:free_cash_flow`) on ingested SEC facts. Cross-period metrics (growth),
ROIC (its NOPAT and invested-capital definitions are not yet approved), the
filing XBRL instances needed to classify 8-K recasts, older paginated
submissions history, market data and AI research are not built.
