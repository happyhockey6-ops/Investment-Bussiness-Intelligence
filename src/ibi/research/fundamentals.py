"""Annual company fundamentals as reported to the SEC, with provenance.

Read-only and point in time. Every cell is a reported FACT taken from one
annual periodic filing (10-K or 10-K/A); nothing is calculated, estimated
or combined across filings here. Derived metrics (gross margin, free cash
flow) are read, never recomputed, from `financial_engine.results_store`.

Selection rule (statement version "1"), for one line item and one fiscal
year (an annual duration ending on the fiscal year-end date):

1. Consider facts available by `as_of` from 10-K/10-K/A filings, excluding
   quarantined facts.
2. Within one filing, the first listed tag present wins; tags that report
   different values within that filing make the cell CONFLICTING.
3. The cell shows the value from the most recently available filing that
   reports it ("as currently reported"). If the first filing to report it
   gave a different value, the cell is RESTATED and keeps that original
   value and its filing as well.

A change to the line items, tags or rule is a new `STATEMENT_VERSION`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from ibi.core.types import is_uncertain
from ibi.db.models import FilingRecord, FinancialDataPointRecord
from ibi.financial_engine import results_store
from ibi.financial_engine.policy import POLICY_V1

STATEMENT_VERSION = "1"
ANNUAL_FORMS = ("10-K", "10-K/A")
ANNUAL_DAYS = (350, 378)  # same bounds as POLICY_V1's "annual" period class
MAX_YEARS = 10


class Statement(StrEnum):
    INCOME = "income"
    CASH_FLOW = "cash_flow"
    BALANCE_SHEET = "balance_sheet"


@dataclass(frozen=True, slots=True)
class LineItem:
    key: str
    label: str
    statement: Statement
    tags: tuple[str, ...]
    unit: str
    instant: bool = False


def _policy_tags(concept: str) -> tuple[str, ...]:
    """Reuse the reviewed financial_engine tag lists, so a line item and the
    metric computed from it can never read different tags."""
    return POLICY_V1.concept(concept).tags


LINE_ITEMS: tuple[LineItem, ...] = (
    LineItem("revenue", "Revenue", Statement.INCOME, _policy_tags("revenue"), "USD"),
    LineItem(
        "cost_of_revenue", "Cost of revenue", Statement.INCOME,
        _policy_tags("cost_of_revenue"), "USD",
    ),
    LineItem("gross_profit", "Gross profit", Statement.INCOME, _policy_tags("gross_profit"), "USD"),
    LineItem(
        "operating_income", "Operating income", Statement.INCOME,
        ("us-gaap:OperatingIncomeLoss",), "USD",
    ),
    LineItem("net_income", "Net income", Statement.INCOME, ("us-gaap:NetIncomeLoss",), "USD"),
    LineItem(
        "eps_diluted", "Diluted EPS", Statement.INCOME,
        ("us-gaap:EarningsPerShareDiluted",), "USD/shares",
    ),
    LineItem(
        "diluted_shares", "Diluted shares", Statement.INCOME,
        ("us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding",), "shares",
    ),
    LineItem(
        "operating_cash_flow", "Operating cash flow", Statement.CASH_FLOW,
        _policy_tags("operating_cash_flow"), "USD",
    ),
    LineItem(
        "capital_expenditures", "Capital expenditures", Statement.CASH_FLOW,
        _policy_tags("capital_expenditures"), "USD",
    ),
    LineItem(
        "cash", "Cash and equivalents", Statement.BALANCE_SHEET,
        ("us-gaap:CashAndCashEquivalentsAtCarryingValue",), "USD", instant=True,
    ),
    LineItem(
        "total_assets", "Total assets", Statement.BALANCE_SHEET,
        ("us-gaap:Assets",), "USD", instant=True,
    ),
    LineItem(
        "long_term_debt", "Long-term debt", Statement.BALANCE_SHEET,
        ("us-gaap:LongTermDebtNoncurrent", "us-gaap:LongTermDebt"), "USD", instant=True,
    ),
)

DERIVED_METRICS = (("ibi:gross_margin", "Gross margin"), ("ibi:free_cash_flow", "Free cash flow"))


class CellStatus(StrEnum):
    VALUE = "value"
    RESTATED = "restated"
    CONFLICTING = "conflicting"
    MISSING = "missing"


@dataclass(frozen=True, slots=True)
class Source:
    accession_number: str
    form_type: str | None
    tag: str
    value: Decimal
    known_available_at: datetime
    fact_id: int
    url: str


@dataclass(frozen=True, slots=True)
class Cell:
    status: CellStatus
    value: Decimal | None = None
    source: Source | None = None
    original: Source | None = None
    """For RESTATED cells: the first filing's report of this value."""
    conflicting: tuple[Source, ...] = ()


@dataclass(frozen=True, slots=True)
class FiscalYear:
    start: date
    end: date

    @property
    def label(self) -> str:
        return f"FY{self.end.year}"


@dataclass(frozen=True, slots=True)
class DerivedCell:
    status: str  # "value" | an uncertainty class name | "not_computed"
    value: Decimal | None = None
    detail: str = ""


@dataclass(slots=True)
class Fundamentals:
    entity_id: str
    as_of: datetime
    statement_version: str
    years: list[FiscalYear]
    rows: dict[str, dict[date, Cell]] = field(default_factory=dict)
    derived: dict[str, dict[date, DerivedCell]] = field(default_factory=dict)

    @property
    def has_data(self) -> bool:
        return bool(self.years)


def filing_url(cik: int, accession: str, primary_document: str | None) -> str:
    folder = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}"
    return f"{folder}/{primary_document}" if primary_document else f"{folder}/{accession}-index.htm"


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _cell(item: LineItem, facts: list[FinancialDataPointRecord], source_of) -> Cell:
    if not facts:
        return Cell(CellStatus.MISSING)
    priority = {tag: i for i, tag in enumerate(item.tags)}
    by_accession: dict[str, list[FinancialDataPointRecord]] = {}
    for f in facts:
        by_accession.setdefault(f.accession_number or "", []).append(f)
    chosen: list[tuple[datetime, str, FinancialDataPointRecord]] = []
    for accession, rows in by_accession.items():
        if len({Decimal(r.value) for r in rows}) > 1:
            # Interchangeable tags (or a repeated tag) disagree inside one
            # filing: never pick one.
            ordered = sorted(rows, key=lambda r: (priority[r.metric_id], r.id))
            return Cell(CellStatus.CONFLICTING, conflicting=tuple(source_of(r) for r in ordered))
        best = min(rows, key=lambda r: (priority[r.metric_id], r.id))
        chosen.append((_utc(best.known_available_at), accession, best))
    chosen.sort(key=lambda t: (t[0], t[1]))
    first, latest = chosen[0][2], chosen[-1][2]
    if Decimal(first.value) != Decimal(latest.value):
        return Cell(
            CellStatus.RESTATED, value=Decimal(latest.value),
            source=source_of(latest), original=source_of(first),
        )
    return Cell(CellStatus.VALUE, value=Decimal(latest.value), source=source_of(latest))


def annual_fundamentals(
    session: Session, entity_id: str, cik: int, as_of: datetime | None = None,
    *, max_years: int = MAX_YEARS,
) -> Fundamentals:
    as_of = _utc(as_of) if as_of is not None else datetime.now(UTC)
    tags = sorted({t for item in LINE_ITEMS for t in item.tags})
    quarantined = results_store.quarantined_fact_ids()
    facts = session.execute(
        select(FinancialDataPointRecord).where(
            FinancialDataPointRecord.entity_id == entity_id,
            FinancialDataPointRecord.metric_id.in_(tags),
            FinancialDataPointRecord.epistemic_label == "fact",
            FinancialDataPointRecord.source_form_type.in_(ANNUAL_FORMS),
            FinancialDataPointRecord.known_available_at <= as_of,
            FinancialDataPointRecord.value.is_not(None),
            FinancialDataPointRecord.id.not_in(quarantined),
        )
    ).scalars().all()

    documents = dict(session.execute(
        select(FilingRecord.accession_number, FilingRecord.primary_document)
        .where(FilingRecord.entity_id == entity_id)
    ).all())

    def source_of(f: FinancialDataPointRecord) -> Source:
        accession = f.accession_number or ""
        return Source(
            accession_number=accession, form_type=f.source_form_type, tag=f.metric_id,
            value=Decimal(f.value), known_available_at=_utc(f.known_available_at),
            fact_id=f.id, url=filing_url(cik, accession, documents.get(accession)),
        )

    duration_items = [i for i in LINE_ITEMS if not i.instant]
    duration_tags = {t: i for i in duration_items for t in i.tags}
    years = sorted(
        {
            FiscalYear(f.start_date, f.period_end_date) for f in facts
            if f.start_date is not None and f.metric_id in duration_tags
            and f.unit == duration_tags[f.metric_id].unit
            and ANNUAL_DAYS[0] <= (f.period_end_date - f.start_date).days <= ANNUAL_DAYS[1]
        },
        key=lambda y: y.end, reverse=True,
    )
    # Two different annual durations ending on the same date (e.g. a stub
    # around a fiscal-year change) cannot both be "the" fiscal year: keep
    # the longest and leave the other out rather than mixing them.
    by_end: dict[date, FiscalYear] = {}
    for y in years:
        if y.end not in by_end or (y.end - y.start) > (by_end[y.end].end - by_end[y.end].start):
            by_end[y.end] = y
    years = sorted(by_end.values(), key=lambda y: y.end, reverse=True)[:max_years]

    result = Fundamentals(entity_id, as_of, STATEMENT_VERSION, years)
    for item in LINE_ITEMS:
        row: dict[date, Cell] = {}
        for y in years:
            matching = [
                f for f in facts
                if f.metric_id in item.tags and f.unit == item.unit
                and f.period_end_date == y.end
                and ((f.start_date is None) if item.instant else (f.start_date == y.start))
            ]
            row[y.end] = _cell(item, matching, source_of)
        result.rows[item.key] = row

    for metric_id, _ in DERIVED_METRICS:
        versions = results_store.VersionPair("1", "1")
        derived: dict[date, DerivedCell] = {}
        for y in years:
            answer = results_store.read_current(
                session, entity_id, metric_id, y.start, y.end, as_of, versions=versions
            )
            if isinstance(answer, results_store.ResolvedMetric):
                derived[y.end] = DerivedCell("value", answer.value)
            elif is_uncertain(answer):
                code = getattr(answer, "reason_code", None)
                derived[y.end] = DerivedCell(
                    type(answer).__name__, detail=(code.value if code else answer.reason)
                )
            else:  # pragma: no cover - readers only return the two shapes above
                derived[y.end] = DerivedCell("not_computed")
        result.derived[metric_id] = derived
    return result
