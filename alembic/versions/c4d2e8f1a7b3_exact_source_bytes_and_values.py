"""Exact SEC source bytes and exact fact values.

Revision ID: c4d2e8f1a7b3
Revises: b91c7e2a4d60

- `source_documents.content_sha256` / `content_length` (nullable): digest and
  size of the exact archived response bytes. Existing rows stay NULL: their
  archives were re-serialized JSON, so no honest digest of the original
  response exists, and none is invented.
- `financial_data.value`: NUMERIC(24,6) -> unconstrained NUMERIC, so values
  with more than six decimal places are stored exactly instead of rounded.
  Widening only: every existing value is representable unchanged.

Downgrade refuses, without changing anything, if it would destroy evidence:
any recorded digest, or any value that NUMERIC(24,6) cannot hold exactly.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4d2e8f1a7b3"
down_revision: str | None = "b91c7e2a4d60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("source_documents") as batch_op:
        batch_op.add_column(sa.Column("content_sha256", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("content_length", sa.BigInteger(), nullable=True))
    with op.batch_alter_table("financial_data") as batch_op:
        batch_op.alter_column(
            "value", existing_type=sa.Numeric(24, 6), type_=sa.Numeric(), existing_nullable=True
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("LOCK TABLE source_documents, financial_data IN ACCESS EXCLUSIVE MODE")
        inexact = bind.execute(sa.text(
            "SELECT count(*) FROM financial_data WHERE value IS NOT NULL AND "
            "(value <> round(value, 6) OR abs(value) >= 1e18)"
        )).scalar_one()
    else:
        # SQLite stores NUMERIC with REAL/INTEGER affinity and never enforced
        # the scale, so narrowing it there cannot lose more than it already has.
        inexact = 0
    hashed = bind.execute(sa.text(
        "SELECT count(*) FROM source_documents WHERE content_sha256 IS NOT NULL"
    )).scalar_one()
    if hashed or inexact:
        raise RuntimeError(
            f"Cannot downgrade c4d2e8f1a7b3: {hashed} source document digest(s) and "
            f"{inexact} fact value(s) not representable as NUMERIC(24,6) would be lost; "
            "refusing to destroy evidence."
        )
    with op.batch_alter_table("financial_data") as batch_op:
        batch_op.alter_column(
            "value", existing_type=sa.Numeric(), type_=sa.Numeric(24, 6), existing_nullable=True
        )
    with op.batch_alter_table("source_documents") as batch_op:
        batch_op.drop_column("content_length")
        batch_op.drop_column("content_sha256")
