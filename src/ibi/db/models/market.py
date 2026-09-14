from __future__ import annotations

from datetime import date

from sqlalchemy import BigInteger, Date, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from ibi.db.base import Base, TimestampMixin


class MarketDataBarRecord(TimestampMixin, Base):
    """One daily price/volume bar for an entity, tagged with the provider
    that supplied it (see `ibi.providers.market_data`) — no vendor is
    hard-coded; `provider_name` is data, not a schema assumption."""

    __tablename__ = "market_data"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[str] = mapped_column(ForeignKey("entities.entity_id"))
    provider_name: Mapped[str] = mapped_column(String(64))
    as_of_date: Mapped[date] = mapped_column(Date)
    open: Mapped[Numeric] = mapped_column(Numeric(18, 6))
    high: Mapped[Numeric] = mapped_column(Numeric(18, 6))
    low: Mapped[Numeric] = mapped_column(Numeric(18, 6))
    close: Mapped[Numeric] = mapped_column(Numeric(18, 6))
    volume: Mapped[int] = mapped_column(BigInteger)
