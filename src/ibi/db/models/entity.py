from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from ibi.db.base import Base, TimestampMixin


class EntityRecord(TimestampMixin, Base):
    """Canonical entity identity. Every other table references
    `entity_id`, never a free-text name — see `ibi.entity_engine`."""

    __tablename__ = "entities"

    entity_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    canonical_name: Mapped[str] = mapped_column(String(255))
