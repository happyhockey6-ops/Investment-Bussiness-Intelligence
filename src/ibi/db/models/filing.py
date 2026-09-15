from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from ibi.db.base import Base, TimestampMixin


class FilingRecord(TimestampMixin, Base):
    """Metadata for a single SEC filing (or, in future, another provider's
    equivalent regulatory filing — kept provider-neutral: see `source`).

    Distinct from `financial_data.source_document_id`, which points to the
    raw `companyfacts` API snapshot a *value* was read from — this table's
    `source_document_id` points to the raw `submissions` snapshot the
    *filing metadata itself* was read from. Two different provenance edges,
    both required to answer "what filing produced this" precisely (see
    docs/data_architecture.md).

    Phase 1 does not fetch or store the filing's own document bytes
    (HTML/XML/exhibits) — `primary_document` is a filename reference only,
    enough to construct a real sec.gov URL, never a copy of the content.
    """

    __tablename__ = "filings"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[str] = mapped_column(ForeignKey("entities.entity_id"))
    source: Mapped[str] = mapped_column(String(64))
    accession_number: Mapped[str] = mapped_column(String(32), unique=True)
    form_type: Mapped[str] = mapped_column(String(32))
    filing_date: Mapped[date] = mapped_column(Date)
    period_of_report: Mapped[date | None] = mapped_column(Date)
    primary_document: Mapped[str | None] = mapped_column(String(512))
    known_available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    availability_precision: Mapped[str] = mapped_column(String(32))
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("source_documents.id"))
