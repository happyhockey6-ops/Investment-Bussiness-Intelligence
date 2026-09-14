from __future__ import annotations

from sqlalchemy import Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from ibi.db.base import Base, TimestampMixin, portable_json


class ThesisRecord(TimestampMixin, Base):
    """Groups exactly one bear/base/bull `ScenarioRecord` set for an entity
    — see `ibi.thesis_engine.interfaces.Thesis`."""

    __tablename__ = "theses"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[str] = mapped_column(ForeignKey("entities.entity_id"))
    evidence_ids: Mapped[list] = mapped_column(portable_json(), default=list)


class ScenarioRecord(TimestampMixin, Base):
    """One bear, base, or bull scenario belonging to a `ThesisRecord` —
    see `ibi.thesis_engine.interfaces.Scenario`."""

    __tablename__ = "scenarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    thesis_id: Mapped[int] = mapped_column(ForeignKey("theses.id"))
    kind: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[float] = mapped_column(Float)
    epistemic_label: Mapped[str] = mapped_column(String(32))
    assumptions: Mapped[list] = mapped_column(portable_json(), default=list)
    catalysts: Mapped[list] = mapped_column(portable_json(), default=list)
    risks: Mapped[list] = mapped_column(portable_json(), default=list)
    unknowns: Mapped[list] = mapped_column(portable_json(), default=list)
    invalidation_conditions: Mapped[list] = mapped_column(portable_json(), default=list)
