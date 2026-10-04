"""Phase 2B: stored, point-in-time calculation results.

Calculations do NOT live in `financial_data` — that table stays facts-only
(every row a non-null, observed value).

Shape:
- `metric_generations`: one row per build — which exact input snapshot
  (id watermarks + content hash) was used, under which (calculation_version,
  selection_policy_version) pair.
- `metric_results`: the point-in-time history of each (entity, metric,
  version pair, period). Append-only per version pair: because retroactive
  drift is an error (approved C3), a new build may only add epochs after the
  previous build's last epoch, so a row is written once, by the generation
  that first produced it (`generation_id`), and never copied, updated or
  deleted. "Generation g's view" = the pair's rows with generation_id <= g.
- `metric_result_inputs`: provenance — every stored fact that informed a
  result, and how (input, corroborating, conflicting, ...).

Only `ibi.financial_engine.results_store` may read or write these tables
(enforced by tests/unit/financial_engine/test_versions_and_architecture.py)
— downstream code goes through its typed readers, so uncertainty can never
be read as a number or silently replaced by an older value.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ibi.db.base import Base, TimestampMixin, portable_json

RESULT_STATUSES = (
    "value",
    "conflicting_evidence",
    "insufficient_evidence",
    "unverified_revision",
    "incompatible_basis",
)
INPUT_RELATIONS = (
    "input",
    "corroborating",
    "conflicting",
    "unverified_revision",
    "superseded_basis",
    "check_term",
)
REVISION_KINDS = ("amendment", "recast", "comparative_revision_unexplained")
GENERATION_KINDS = ("initial", "new_vintage")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class MetricGenerationRecord(TimestampMixin, Base):
    """One build over one exactly-identified input snapshot.

    The watermarks (max id of `financial_data`, `filings`, `fact_quarantine`
    visible to the build) select the snapshot; `input_set_hash` proves it is
    still reproducible; `policy_manifest_hash` pins the policy content the
    version string meant at build time.
    """

    __tablename__ = "metric_generations"
    __table_args__ = (
        UniqueConstraint(
            "entity_id", "metric_id", "calculation_version", "selection_policy_version",
            "input_set_hash",
            name="uq_metric_generations_inputs",
        ),
        CheckConstraint(_in("kind", GENERATION_KINDS), name="ck_metric_generations_kind"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[str] = mapped_column(ForeignKey("entities.entity_id"))
    metric_id: Mapped[str] = mapped_column(String(64))
    calculation_version: Mapped[str] = mapped_column(String(32))
    selection_policy_version: Mapped[str] = mapped_column(String(32))
    policy_manifest_hash: Mapped[str] = mapped_column(String(64))
    fact_watermark: Mapped[int] = mapped_column(Integer)
    filing_watermark: Mapped[int] = mapped_column(Integer)
    quarantine_watermark: Mapped[int] = mapped_column(Integer)
    input_set_hash: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32))
    max_effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MetricResultRecord(TimestampMixin, Base):
    """The state of one (entity, metric, version pair, period) series from
    `effective_from` onward. Exactly one row per epoch per version pair
    (unique indexes below); a non-`value` status always has a NULL `value`
    and a non-NULL `reason_code` (CHECK constraint), so uncertainty can never
    be summed or averaged as if it were a number."""

    __tablename__ = "metric_results"
    __table_args__ = (
        Index(
            "uq_metric_results_duration_epoch",
            "entity_id", "metric_id", "calculation_version", "selection_policy_version",
            "start_date", "period_end_date", "effective_from",
            unique=True,
            postgresql_where=text("start_date IS NOT NULL"),
            sqlite_where=text("start_date IS NOT NULL"),
        ),
        Index(
            "uq_metric_results_instant_epoch",
            "entity_id", "metric_id", "calculation_version", "selection_policy_version",
            "period_end_date", "effective_from",
            unique=True,
            postgresql_where=text("start_date IS NULL"),
            sqlite_where=text("start_date IS NULL"),
        ),
        CheckConstraint(_in("status", RESULT_STATUSES), name="ck_metric_results_status"),
        CheckConstraint(
            "(status = 'value' AND value IS NOT NULL AND reason_code IS NULL) OR "
            "(status <> 'value' AND value IS NULL AND reason_code IS NOT NULL)",
            name="ck_metric_results_status_value",
        ),
        CheckConstraint("epistemic_label = 'calculation'", name="ck_metric_results_label"),
        CheckConstraint(
            "revision_kind IS NULL OR " + _in("revision_kind", REVISION_KINDS),
            name="ck_metric_results_revision_kind",
        ),
        CheckConstraint(
            _in("availability_precision", ("acceptance_timestamp", "day_conservative")),
            name="ck_metric_results_precision",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    generation_id: Mapped[int] = mapped_column(ForeignKey("metric_generations.id"))
    entity_id: Mapped[str] = mapped_column(ForeignKey("entities.entity_id"))
    metric_id: Mapped[str] = mapped_column(String(64))
    calculation_version: Mapped[str] = mapped_column(String(32))
    selection_policy_version: Mapped[str] = mapped_column(String(32))
    unit: Mapped[str] = mapped_column(String(32))
    start_date: Mapped[date | None] = mapped_column(Date)
    period_end_date: Mapped[date] = mapped_column(Date)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    availability_precision: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    reason_code: Mapped[str | None] = mapped_column(String(64))
    value: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    basis_accession: Mapped[str | None] = mapped_column(String(32))
    revision_kind: Mapped[str | None] = mapped_column(String(48))
    check_outcomes: Mapped[dict] = mapped_column(portable_json())
    result_hash: Mapped[str] = mapped_column(String(64))
    epistemic_label: Mapped[str] = mapped_column(String(32))


class MetricResultInputRecord(TimestampMixin, Base):
    """Provenance: links a result to every stored fact that informed it, and
    how (`relation`) — inputs, but also corroborating, conflicting,
    unverified-revision, superseded-basis and identity-check facts."""

    __tablename__ = "metric_result_inputs"
    __table_args__ = (
        UniqueConstraint(
            "result_id", "fact_id", "role", "relation", name="uq_metric_result_inputs"
        ),
        CheckConstraint(_in("relation", INPUT_RELATIONS), name="ck_metric_result_inputs_relation"),
        Index("ix_metric_result_inputs_fact_id", "fact_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    result_id: Mapped[int] = mapped_column(ForeignKey("metric_results.id"))
    fact_id: Mapped[int] = mapped_column(ForeignKey("financial_data.id"))
    role: Mapped[str] = mapped_column(String(64))
    relation: Mapped[str] = mapped_column(String(32))


class MetricVersionActivationRecord(TimestampMixin, Base):
    """Append-only: which (calculation_version, selection_policy_version)
    pair was designated active for a metric from `created_at` onward. Used
    only to *look up* a pair (`results_store.active_versions`); every read
    still takes the pair explicitly."""

    __tablename__ = "metric_version_activations"

    id: Mapped[int] = mapped_column(primary_key=True)
    metric_id: Mapped[str] = mapped_column(String(64))
    calculation_version: Mapped[str] = mapped_column(String(32))
    selection_policy_version: Mapped[str] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(Text)


class FactQuarantineRecord(TimestampMixin, Base):
    """Append-only: a stored fact excluded from every input snapshot built
    after this row exists. The fact itself is never edited or deleted."""

    __tablename__ = "fact_quarantine"
    __table_args__ = (UniqueConstraint("fact_id", name="uq_fact_quarantine_fact_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    fact_id: Mapped[int] = mapped_column(ForeignKey("financial_data.id"))
    reason: Mapped[str] = mapped_column(Text)
