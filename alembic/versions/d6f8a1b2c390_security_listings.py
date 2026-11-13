"""Append-only SEC security-listing snapshots.

Revision ID: d6f8a1b2c390
Revises: c4d2e8f1a7b3

Different CIKs for one ticker are retained as conflicting evidence. No
foreign key to entities: directory membership does not ingest company facts.
BIGINT preserves the full ten-digit integer CIK range without truncation.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d6f8a1b2c390"
down_revision: str | None = "c4d2e8f1a7b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "security_listings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "source_document_id", sa.Integer(), sa.ForeignKey("source_documents.id"), nullable=False
        ),
        sa.Column("cik", sa.BigInteger(), nullable=False),
        sa.Column("entity_id", sa.String(13), nullable=False),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("raw_ticker", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("exchange", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "source_document_id", "ticker", "cik", name="uq_security_listings_snapshot_ticker_cik"
        ),
    )
    op.create_index("ix_security_listings_ticker", "security_listings", ["ticker"])


def downgrade() -> None:
    # Drops only this snapshot's listing table/index, never archived evidence.
    op.drop_table("security_listings")
