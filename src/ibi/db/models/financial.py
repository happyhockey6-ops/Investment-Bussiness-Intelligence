from __future__ import annotations

from datetime import date

from sqlalchemy import Date, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from ibi.db.base import Base, TimestampMixin


class FinancialDataPointRecord(TimestampMixin, Base):
    """One computed or reported financial metric value for an entity/period.

    `calculation_version` identifies the exact `financial_engine` formula
    version that produced a CALCULATION row — required so a later formula
    change never silently reinterprets a historical value (see
    `ibi.financial_engine`)."""

    __tablename__ = "financial_data"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[str] = mapped_column(ForeignKey("entities.entity_id"))
    metric_id: Mapped[str] = mapped_column(String(64))
    period_end_date: Mapped[date] = mapped_column(Date)
    value: Mapped[Numeric | None] = mapped_column(Numeric(24, 6))
    epistemic_label: Mapped[str] = mapped_column(String(32))
    calculation_version: Mapped[str | None] = mapped_column(String(32))
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("source_documents.id"))
