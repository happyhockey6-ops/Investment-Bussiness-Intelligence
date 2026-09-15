"""phase1 financial_data identity partial unique indexes

Revision ID: a950c85c80b9
Revises: 50dc0583f9c1
Create Date: 2026-09-14 20:44:20.377560

Two partial unique indexes enforcing the approved XBRL fact identity model
— one per fact shape, rather than a single constraint or a COALESCE
expression, so each index is self-documenting about which shape it governs
(see DECISIONS.md for the full comparison and why partial indexes were
chosen over a COALESCE-based single index or a NULL-sentinel column).

Gated on empirical validation: this identity key was checked against
~58,000 real XBRL fact entries from two real companies (Apple, Microsoft)
before this migration was written, with zero collisions found. See
DECISIONS.md, "XBRL fact identity — empirical validation."
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = 'a950c85c80b9'
down_revision: str | None = '50dc0583f9c1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_financial_data_duration_fact",
        "financial_data",
        ["entity_id", "metric_id", "unit", "start_date", "period_end_date", "accession_number"],
        unique=True,
        postgresql_where=text("start_date IS NOT NULL"),
        sqlite_where=text("start_date IS NOT NULL"),
    )
    op.create_index(
        "uq_financial_data_instant_fact",
        "financial_data",
        ["entity_id", "metric_id", "unit", "period_end_date", "accession_number"],
        unique=True,
        postgresql_where=text("start_date IS NULL"),
        sqlite_where=text("start_date IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_financial_data_instant_fact", table_name="financial_data")
    op.drop_index("uq_financial_data_duration_fact", table_name="financial_data")
