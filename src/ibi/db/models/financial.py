from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from ibi.db.base import Base, TimestampMixin


class FinancialDataPointRecord(TimestampMixin, Base):
    """One computed or reported financial metric value for an entity/period.

    `calculation_version` identifies the exact `financial_engine` formula
    version that produced a CALCULATION row — required so a later formula
    change never silently reinterprets a historical value (see
    `ibi.financial_engine`).

    `metric_id` encodes `"<taxonomy>:<tag>"` (e.g. `"us-gaap:Revenues"`) for
    XBRL-sourced rows — reusing this existing column rather than adding
    separate `taxonomy`/`tag` columns keeps the Phase 1 migration additive
    and minimal. `period_end_date` doubles as the XBRL "end" date; `start_date`
    is populated only for duration-type facts (absent/NULL for instant
    facts, e.g. balance-sheet items) — see docs/data_architecture.md for why
    both `unit` and `start_date` are required to distinguish genuinely
    different XBRL facts (empirically validated: without them, thousands of
    real facts collide — e.g. an annual and a Q4-only duration reported
    under the same tag/end-date/accession).

    `known_available_at`/`availability_precision` implement the approved
    point-in-time rule (see DECISIONS.md) — deliberately distinct from
    `created_at` (this row's own insert time) and `source_documents.retrieval_date`
    (when the *source* was fetched), neither of which is a proxy for when
    this fact was publicly knowable.
    """

    __tablename__ = "financial_data"
    __table_args__ = (
        # Two partial unique indexes, one per XBRL fact shape, rather than a
        # single constraint — kept identical to the hand-written migration
        # a950c85c80b9 (see DECISIONS.md for why partial indexes were chosen
        # over a COALESCE expression or a NULL-sentinel column).
        Index(
            "uq_financial_data_duration_fact",
            "entity_id", "metric_id", "unit", "start_date", "period_end_date", "accession_number",
            unique=True,
            postgresql_where=text("start_date IS NOT NULL"),
            sqlite_where=text("start_date IS NOT NULL"),
        ),
        Index(
            "uq_financial_data_instant_fact",
            "entity_id", "metric_id", "unit", "period_end_date", "accession_number",
            unique=True,
            postgresql_where=text("start_date IS NULL"),
            sqlite_where=text("start_date IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[str] = mapped_column(ForeignKey("entities.entity_id"))
    metric_id: Mapped[str] = mapped_column(String(64))
    unit: Mapped[str] = mapped_column(String(32))
    start_date: Mapped[date | None] = mapped_column(Date)
    period_end_date: Mapped[date] = mapped_column(Date)
    value: Mapped[Numeric | None] = mapped_column(Numeric(24, 6))
    epistemic_label: Mapped[str] = mapped_column(String(32))
    calculation_version: Mapped[str | None] = mapped_column(String(32))
    accession_number: Mapped[str | None] = mapped_column(String(32))
    known_available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    availability_precision: Mapped[str | None] = mapped_column(String(32))
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("source_documents.id"))
