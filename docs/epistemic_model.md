# The Epistemic Model

Every piece of information the platform stores or produces is labeled with
what *kind* of claim it is. This is the mechanism that keeps AI
interpretation from silently becoming a source of financial truth.

## The labels

Defined in `ibi.core.epistemics.EpistemicLabel`:

| Label | Meaning | Who can produce it |
|---|---|---|
| `FACT` | Directly observed/verifiable in a primary source | Ingested from a `PRIMARY_REGULATORY` or `PRIMARY_COMPANY` source only |
| `REPORTED` | Stated by a source, not independently verified | Any ingestion |
| `CALCULATION` | Deterministically derived by a documented, versioned formula | `financial_engine` (and future `market_engine`) only — never an AI provider |
| `INFERENCE` | A conclusion drawn from evidence, not itself observed | Human or AI |
| `ASSUMPTION` | An input taken as given, explicitly flagged as unverified | Human or AI |
| `PREDICTION` | A statement about a not-yet-observable future state | Human or AI |
| `SPECULATION` | A low-confidence hypothesis, not a stood-behind prediction | Human or AI |

`ibi.core.epistemics.Evidence` enforces one rule at construction time: a
claim cannot be labeled `FACT` if its provenance's `source_tier` is
`AI_GENERATED` or `UNVERIFIED`. This is a deliberately narrow, mechanical
check — it catches the most dangerous failure mode (AI output being stored
as fact) without trying to fully automate epistemic correctness, which
requires judgment `Evidence.__post_init__` cannot exercise.

## Source tiers

`ibi.core.epistemics.SourceTier` — a coarse reliability signal:
`PRIMARY_REGULATORY`, `PRIMARY_COMPANY`, `ESTABLISHED_MEDIA`,
`SECONDARY_ANALYSIS`, `UNVERIFIED`, `AI_GENERATED`. Intentionally coarse in
Phase 0; a full source-reliability model (e.g. per-source historical
accuracy tracking) is future `ai_research_engine`/`knowledge_graph` work.

## Provenance

`ibi.core.epistemics.Provenance` separates three dates that are easy to
conflate but serve different purposes:

- `publication_date` / `observation_date` — describe the world (when the
  event happened / when the source published it).
- `retrieval_date` — describes the system (when this platform learned it).

Keeping `retrieval_date` distinct from the others is what makes
point-in-time backtesting possible: `ibi.backtesting_engine.interfaces.PointInTimeDataset`
filters purely on `retrieval_date <= as_of`, regardless of what the record
claims about when the underlying event occurred. This is also what
protects against using a *restated* figure (e.g. revised GAAP earnings) as
if it had been known at the time of original publication — the restatement
arrives as a new record with a later `retrieval_date`.

## Uncertainty, not fabrication

Alongside epistemic labels, `ibi.core.types` defines the vocabulary for "we
don't have an answer": `Unknown`, `InsufficientEvidence`,
`ConflictingEvidence` (collectively `Uncertain`). Any function that might
not be able to produce a real value should return `T | Uncertain`, not
`None` (which means "does not apply", a different thing) and never a
guessed value. `financial_engine.metrics` demonstrates the pattern: dividing
by zero revenue returns `InsufficientEvidence`, not an exception or a
fabricated ratio.

## Where this shows up in storage

`db.models.evidence.ClaimRecord` / `EvidenceRecord`,
`db.models.financial.FinancialDataPointRecord`, and
`db.models.research.ResearchRecord` all carry an `epistemic_label` column —
every stored claim, calculation, and AI research finding is queryable by
what kind of claim it is, not just its content.
