from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from ibi.db.base import Base, TimestampMixin


class PredictionRecord(TimestampMixin, Base):
    """A dated, falsifiable prediction — see `ibi.learning_engine`."""

    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[str] = mapped_column(ForeignKey("entities.entity_id"))
    statement: Mapped[str] = mapped_column(Text)
    made_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolves_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OutcomeRecord(TimestampMixin, Base):
    """The realized outcome for a `PredictionRecord`. Written once, when the
    prediction resolves; never edited afterward — a revised understanding
    becomes a post-mortem note, not a changed outcome (immutability of the
    historical record)."""

    __tablename__ = "outcomes"

    id: Mapped[int] = mapped_column(primary_key=True)
    prediction_id: Mapped[int] = mapped_column(ForeignKey("predictions.id"))
    actual_result: Mapped[str] = mapped_column(Text)
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    error_notes: Mapped[str | None] = mapped_column(Text)
