"""phase2b metric results, generations, provenance, form type

Revision ID: e7f3a2c91b40
Revises: a950c85c80b9
Create Date: 2026-10-03

Additive only — no existing table, column, index or row is modified or
dropped, and no data is written or backfilled:

- `financial_data.source_form_type` (nullable): the SEC form of the fact's
  filing. Rows ingested before this migration keep NULL; the Phase 2B
  resolver treats NULL as an unclassified form (fail closed). They are never
  backfilled without a separately approved, provenance-verified procedure.
- `filings.items` (nullable): 8-K item codes from submissions.json.
- Five new tables: `metric_generations`, `metric_results`,
  `metric_result_inputs`, `metric_version_activations`, `fact_quarantine`
  — see `ibi.db.models.metric_result`. CHECK constraints are added to the
  new tables only (approved C5), all portable to SQLite and PostgreSQL.

Supersedes an earlier, never-committed draft of this migration
(c2b7e4f19a6d). A new revision id was used deliberately: if that draft was
ever applied to a database, Alembic now fails loudly ("Can't locate
revision c2b7e4f19a6d") instead of silently treating a different schema as
current.

Hand-written (no live PostgreSQL reachable at authoring time); kept
identical to the ORM models, verified by `alembic check` against a fresh
SQLite database and by tests/integration/test_alembic_migration.py.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e7f3a2c91b40"
down_revision: str | None = "a950c85c80b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUSES = (
    "value",
    "conflicting_evidence",
    "insufficient_evidence",
    "unverified_revision",
    "incompatible_basis",
)
_RELATIONS = (
    "input",
    "corroborating",
    "conflicting",
    "unverified_revision",
    "superseded_basis",
    "check_term",
)
_REVISION_KINDS = ("amendment", "recast", "comparative_revision_unexplained")
_SERIES = ("entity_id", "metric_id", "calculation_version", "selection_policy_version")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    with op.batch_alter_table("financial_data") as batch_op:
        batch_op.add_column(sa.Column("source_form_type", sa.String(32), nullable=True))
    with op.batch_alter_table("filings") as batch_op:
        batch_op.add_column(sa.Column("items", sa.String(128), nullable=True))

    op.create_table(
        "metric_generations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("entity_id", sa.String(64), sa.ForeignKey("entities.entity_id"), nullable=False),
        sa.Column("metric_id", sa.String(64), nullable=False),
        sa.Column("calculation_version", sa.String(32), nullable=False),
        sa.Column("selection_policy_version", sa.String(32), nullable=False),
        sa.Column("policy_manifest_hash", sa.String(64), nullable=False),
        sa.Column("fact_watermark", sa.Integer, nullable=False),
        sa.Column("filing_watermark", sa.Integer, nullable=False),
        sa.Column("quarantine_watermark", sa.Integer, nullable=False),
        sa.Column("input_set_hash", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("max_effective_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(*_SERIES, "input_set_hash", name="uq_metric_generations_inputs"),
        sa.CheckConstraint(
            _in("kind", ("initial", "new_vintage")), name="ck_metric_generations_kind"
        ),
    )

    op.create_table(
        "metric_results",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "generation_id", sa.Integer, sa.ForeignKey("metric_generations.id"), nullable=False
        ),
        sa.Column("entity_id", sa.String(64), sa.ForeignKey("entities.entity_id"), nullable=False),
        sa.Column("metric_id", sa.String(64), nullable=False),
        sa.Column("calculation_version", sa.String(32), nullable=False),
        sa.Column("selection_policy_version", sa.String(32), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("start_date", sa.Date, nullable=True),
        sa.Column("period_end_date", sa.Date, nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("availability_precision", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=True),
        sa.Column("value", sa.Numeric(28, 10), nullable=True),
        sa.Column("basis_accession", sa.String(32), nullable=True),
        sa.Column("revision_kind", sa.String(48), nullable=True),
        sa.Column(
            "check_outcomes",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("result_hash", sa.String(64), nullable=False),
        sa.Column("epistemic_label", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_in("status", _STATUSES), name="ck_metric_results_status"),
        sa.CheckConstraint(
            "(status = 'value' AND value IS NOT NULL AND reason_code IS NULL) OR "
            "(status <> 'value' AND value IS NULL AND reason_code IS NOT NULL)",
            name="ck_metric_results_status_value",
        ),
        sa.CheckConstraint("epistemic_label = 'calculation'", name="ck_metric_results_label"),
        sa.CheckConstraint(
            "revision_kind IS NULL OR " + _in("revision_kind", _REVISION_KINDS),
            name="ck_metric_results_revision_kind",
        ),
        sa.CheckConstraint(
            _in("availability_precision", ("acceptance_timestamp", "day_conservative")),
            name="ck_metric_results_precision",
        ),
    )
    op.create_index(
        "uq_metric_results_duration_epoch",
        "metric_results",
        [*_SERIES, "start_date", "period_end_date", "effective_from"],
        unique=True,
        postgresql_where=sa.text("start_date IS NOT NULL"),
        sqlite_where=sa.text("start_date IS NOT NULL"),
    )
    op.create_index(
        "uq_metric_results_instant_epoch",
        "metric_results",
        [*_SERIES, "period_end_date", "effective_from"],
        unique=True,
        postgresql_where=sa.text("start_date IS NULL"),
        sqlite_where=sa.text("start_date IS NULL"),
    )

    op.create_table(
        "metric_result_inputs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("result_id", sa.Integer, sa.ForeignKey("metric_results.id"), nullable=False),
        sa.Column("fact_id", sa.Integer, sa.ForeignKey("financial_data.id"), nullable=False),
        sa.Column("role", sa.String(64), nullable=False),
        sa.Column("relation", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "result_id", "fact_id", "role", "relation", name="uq_metric_result_inputs"
        ),
        sa.CheckConstraint(_in("relation", _RELATIONS), name="ck_metric_result_inputs_relation"),
    )
    op.create_index("ix_metric_result_inputs_fact_id", "metric_result_inputs", ["fact_id"])

    op.create_table(
        "metric_version_activations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("metric_id", sa.String(64), nullable=False),
        sa.Column("calculation_version", sa.String(32), nullable=False),
        sa.Column("selection_policy_version", sa.String(32), nullable=False),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "fact_quarantine",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("fact_id", sa.Integer, sa.ForeignKey("financial_data.id"), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("fact_id", name="uq_fact_quarantine_fact_id"),
    )


def downgrade() -> None:
    op.drop_table("fact_quarantine")
    op.drop_table("metric_version_activations")
    op.drop_index("ix_metric_result_inputs_fact_id", table_name="metric_result_inputs")
    op.drop_table("metric_result_inputs")
    op.drop_index("uq_metric_results_instant_epoch", table_name="metric_results")
    op.drop_index("uq_metric_results_duration_epoch", table_name="metric_results")
    op.drop_table("metric_results")
    op.drop_table("metric_generations")
    with op.batch_alter_table("filings") as batch_op:
        batch_op.drop_column("items")
    with op.batch_alter_table("financial_data") as batch_op:
        batch_op.drop_column("source_form_type")
