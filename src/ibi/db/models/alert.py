from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ibi.db.base import Base, TimestampMixin


class AlertRecord(TimestampMixin, Base):
    """A triggered alert — see `ibi.alert_engine.interfaces.Alert`."""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[str] = mapped_column(ForeignKey("entities.entity_id"))
    message: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(16))
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
