"""phase1 sec edgar filings and financial_data columns

Revision ID: 50dc0583f9c1
Revises: 50445f5a9e59
Create Date: 2026-09-14 20:44:19.234884

Additive only — the Phase 0 migration (50445f5a9e59) is not modified.

Adds the `filings` table (accession_number UNIQUE — safe immediately, SEC
accession numbers are globally unique by construction) and the columns on
`financial_data` needed for the XBRL identity model and the approved
point-in-time rule (`unit`, `start_date`, `accession_number`,
`known_available_at`, `availability_precision`). Deliberately does NOT add
the two partial unique indexes on `financial_data` yet — see the follow-up
migration a950c85c80b9, gated on the empirical identity-key validation
(DECISIONS.md), which has separately already succeeded against real data
before this migration was written.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '50dc0583f9c1'
down_revision: str | None = '50445f5a9e59'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "filings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("entity_id", sa.String(64), sa.ForeignKey("entities.entity_id"), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("accession_number", sa.String(32), nullable=False, unique=True),
        sa.Column("form_type", sa.String(32), nullable=False),
        sa.Column("filing_date", sa.Date, nullable=False),
        sa.Column("period_of_report", sa.Date, nullable=True),
        sa.Column("primary_document", sa.String(512), nullable=True),
        sa.Column("known_available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("availability_precision", sa.String(32), nullable=False),
        sa.Column(
            "source_document_id", sa.Integer, sa.ForeignKey("source_documents.id"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # batch_alter_table: SQLite cannot ALTER COLUMN ... DROP DEFAULT (its
    # ALTER TABLE support is limited to add/rename column); batch mode
    # recreates the table transparently on SQLite while executing the same
    # operations directly on PostgreSQL. This keeps one migration source
    # correct on both dialects rather than branching on dialect name.
    with op.batch_alter_table("financial_data") as batch_op:
        batch_op.add_column(
            sa.Column("unit", sa.String(32), nullable=False, server_default="USD")
        )
        batch_op.add_column(sa.Column("start_date", sa.Date, nullable=True))
        batch_op.add_column(sa.Column("accession_number", sa.String(32), nullable=True))
        batch_op.add_column(
            sa.Column("known_available_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(sa.Column("availability_precision", sa.String(32), nullable=True))

    with op.batch_alter_table("financial_data") as batch_op:
        batch_op.alter_column("unit", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("financial_data") as batch_op:
        batch_op.drop_column("availability_precision")
        batch_op.drop_column("known_available_at")
        batch_op.drop_column("accession_number")
        batch_op.drop_column("start_date")
        batch_op.drop_column("unit")
    op.drop_table("filings")
