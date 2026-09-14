from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ibi.db.base import Base, TimestampMixin


class ResearchRecord(TimestampMixin, Base):
    """One AI-assisted research question/answer, with full provider
    attribution — see `ibi.ai_research_engine` and `ibi.providers.ai`.
    Always epistemically INFERENCE/PREDICTION/SPECULATION, never FACT."""

    __tablename__ = "research"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[str] = mapped_column(ForeignKey("entities.entity_id"))
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    epistemic_label: Mapped[str] = mapped_column(String(32))
    tier: Mapped[str] = mapped_column(String(16))
    provider_name: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))
    confidence: Mapped[float | None] = mapped_column(Float)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
