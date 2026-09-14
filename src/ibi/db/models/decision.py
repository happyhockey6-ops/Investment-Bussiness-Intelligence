from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ibi.db.base import Base, TimestampMixin


class DecisionRecord(TimestampMixin, Base):
    """A recorded decision state — see
    `ibi.decision_engine.interfaces.Decision`. Note there is deliberately no
    BUY/SELL state and no execution linkage: this table records judgment,
    not trades."""

    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[str] = mapped_column(ForeignKey("entities.entity_id"))
    state: Mapped[str] = mapped_column(String(32))
    rationale: Mapped[str] = mapped_column(Text)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    supporting_thesis_id: Mapped[int | None] = mapped_column(ForeignKey("theses.id"))
