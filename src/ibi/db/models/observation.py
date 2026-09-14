from __future__ import annotations

from datetime import date

from sqlalchemy import Date, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from ibi.db.base import Base, TimestampMixin, portable_json


class ObservationRecord(TimestampMixin, Base):
    """A single generic fact about an entity as of a point in time, e.g.
    {"key": "employee_count", "value": 12000}. Purpose-built tables
    (`financial_data`, `market_data`) exist for high-volume, structured
    data; this table is the catch-all for everything else so `data_engine`
    is not blocked on a schema migration for every new fact type."""

    __tablename__ = "observations"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[str] = mapped_column(ForeignKey("entities.entity_id"))
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("source_documents.id"))
    key: Mapped[str] = mapped_column(String(128))
    value: Mapped[dict] = mapped_column(portable_json())
    epistemic_label: Mapped[str] = mapped_column(String(32))
    observation_date: Mapped[date | None] = mapped_column(Date)
    retrieval_date: Mapped[date] = mapped_column(Date)
