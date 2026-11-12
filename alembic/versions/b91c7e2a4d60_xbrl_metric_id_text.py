"""Store complete SEC XBRL identifiers in financial_data.metric_id.

Revision ID: b91c7e2a4d60
Revises: e7f3a2c91b40

Widen VARCHAR(64) to TEXT without changing values, row ids, nullability or
the two partial unique indexes. Derived metric ids in the result tables
are a separate namespace and are not changed.

Downgrade refuses to narrow while any identifier exceeds 64 characters.
No explicit length cast or substring is used: historical identifiers must
never be silently truncated. PostgreSQL locks the table before checking
so a concurrent long insert cannot slip between the check and the ALTER.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b91c7e2a4d60"
down_revision: str | None = "e7f3a2c91b40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("financial_data") as batch_op:
        batch_op.alter_column(
            "metric_id", existing_type=sa.String(64), type_=sa.Text(), existing_nullable=False
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("LOCK TABLE financial_data IN ACCESS EXCLUSIVE MODE")
    oversized = bind.execute(
        sa.text("SELECT count(*) FROM financial_data WHERE length(metric_id) > 64")
    ).scalar_one()
    if oversized:
        raise RuntimeError(
            f"Cannot downgrade financial_data.metric_id: {oversized} identifier(s) exceed "
            "64 characters; refusing to truncate stored identifiers."
        )
    with op.batch_alter_table("financial_data") as batch_op:
        batch_op.alter_column(
            "metric_id", existing_type=sa.Text(), type_=sa.String(64), existing_nullable=False
        )
