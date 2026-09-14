from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ibi.db.base import Base, TimestampMixin


class SourceDocumentRecord(TimestampMixin, Base):
    """An immutable, provenance-tagged reference to an external document
    (filing, article, press release, ...). Content is referenced by
    `source_url`/`content_ref`, not duplicated wholesale into the database,
    to keep this table lightweight — the raw bytes live wherever
    `data_engine` chooses to archive them."""

    __tablename__ = "source_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(255))
    source_tier: Mapped[str] = mapped_column(String(32))
    source_url: Mapped[str | None] = mapped_column(String(2048))
    content_ref: Mapped[str | None] = mapped_column(String(2048))
    publication_date: Mapped[date | None] = mapped_column(Date)
    retrieval_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
