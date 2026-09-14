from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from ibi.db.base import Base, TimestampMixin


class ProviderCallRecord(TimestampMixin, Base):
    """Audit log of every AI/market-data provider call — the "which
    provider, which model, when" half of the observability requirement in
    SECURITY.md/ARCHITECTURE.md. Not written to automatically in Phase 0
    (no live provider call sites exist yet); the table exists so the first
    real call site has somewhere to write to."""

    __tablename__ = "provider_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider_type: Mapped[str] = mapped_column(String(32))  # "ai" | "market_data"
    provider_name: Mapped[str] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128))
    operation: Mapped[str] = mapped_column(String(128))
    called_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Numeric | None] = mapped_column(Numeric(10, 4))
