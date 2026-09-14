from __future__ import annotations

from sqlalchemy import Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ibi.db.base import Base, TimestampMixin


class ClaimRecord(TimestampMixin, Base):
    """A single stated claim about an entity, labeled with an epistemic
    status — see `ibi.core.epistemics.EpistemicLabel`."""

    __tablename__ = "claims"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[str] = mapped_column(ForeignKey("entities.entity_id"))
    claim_text: Mapped[str] = mapped_column(Text)
    epistemic_label: Mapped[str] = mapped_column(String(32))


class EvidenceRecord(TimestampMixin, Base):
    """Links a claim to the source document that supports it, with a
    confidence score — the persisted form of `ibi.core.epistemics.Evidence`."""

    __tablename__ = "evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id"))
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("source_documents.id"))
    confidence: Mapped[float | None] = mapped_column(Float)
