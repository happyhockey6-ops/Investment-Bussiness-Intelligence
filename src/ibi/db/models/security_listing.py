"""An immutable row in one SEC-wide listings snapshot; no entity ingestion."""

from __future__ import annotations

from sqlalchemy import BigInteger, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ibi.db.base import Base, TimestampMixin


class SecurityListingRecord(TimestampMixin, Base):
    __tablename__ = "security_listings"
    __table_args__ = (
        UniqueConstraint(
            "source_document_id", "ticker", "cik", name="uq_security_listings_snapshot_ticker_cik"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_document_id: Mapped[int] = mapped_column(ForeignKey("source_documents.id"))
    cik: Mapped[int] = mapped_column(BigInteger)
    entity_id: Mapped[str] = mapped_column(String(13))  # intentionally no FK to entities
    ticker: Mapped[str] = mapped_column(Text, index=True)
    raw_ticker: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    exchange: Mapped[str | None] = mapped_column(Text)
