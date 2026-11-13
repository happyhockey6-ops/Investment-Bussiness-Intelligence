"""Annual company fundamentals as reported to the SEC, with provenance.

Read-only and point in time. Every cell is a reported FACT taken from one
annual periodic filing (10-K or 10-K/A); nothing is calculated, estimated
or combined across filings here. Derived metrics (gross margin, free cash
flow) are read, never recomputed, from `financial_engine.results_store`.

Selection rule (statement version "2"), for one line item and one fiscal
year (an annual duration ending on the fiscal year-end date):

1. Consider facts available by `as_of` from 10-K/10-K/A filings, excluding
   quarantined facts.
2. Resolve the latest annual filing epoch. Disagreement within or between
   filings at that timestamp is CONFLICTING, never accession-order resolved.
   Older conflicts remain separate historical evidence.
3. Later divergent non-annual evidence and item 4.02 non-reliance notices
   make the cell uncertain. Non-annual facts never supply an annual basis.
4. Changed values from eligible annual filings retain the original when
   the first annual epoch was unambiguous.

A change to the line items, tags or rule is a new `STATEMENT_VERSION`.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from ibi.core.types import EvidenceRef, UncertaintyReason, is_uncertain
from ibi.db.models import FilingRecord, FinancialDataPointRecord
from ibi.financial_engine import results_store
from ibi.financial_engine.policy import POLICY_V1, FormTier

STATEMENT_VERSION = "2"
NON_RELIANCE_COVERAGE = "submissions_recent_window_only"
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
        "cost_of_revenue",
        "Cost of revenue",
        Statement.INCOME,
        _policy_tags("cost_of_revenue"),
        "USD",
    ),
    LineItem("gross_profit", "Gross profit", Statement.INCOME, _policy_tags("gross_profit"), "USD"),
    LineItem(
        "operating_income",
        "Operating income",
        Statement.INCOME,
        ("us-gaap:OperatingIncomeLoss",),
        "USD",
    ),
    LineItem("net_income", "Net income", Statement.INCOME, ("us-gaap:NetIncomeLoss",), "USD"),
    LineItem(
        "eps_diluted",
        "Diluted EPS",
        Statement.INCOME,
        ("us-gaap:EarningsPerShareDiluted",),
        "USD/shares",
    ),
    LineItem(
        "diluted_shares",
        "Diluted shares",
        Statement.INCOME,
        ("us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding",),
        "shares",
    ),
    LineItem(
        "operating_cash_flow",
        "Operating cash flow",
        Statement.CASH_FLOW,
        _policy_tags("operating_cash_flow"),
        "USD",
    ),
    LineItem(
        "capital_expenditures",
        "Capital expenditures",
        Statement.CASH_FLOW,
        _policy_tags("capital_expenditures"),
        "USD",
    ),
    LineItem(
        "cash",
        "Cash and equivalents",
        Statement.BALANCE_SHEET,
        ("us-gaap:CashAndCashEquivalentsAtCarryingValue",),
        "USD",
        instant=True,
    ),
    LineItem(
        "total_assets",
        "Total assets",
        Statement.BALANCE_SHEET,
        ("us-gaap:Assets",),
        "USD",
        instant=True,
    ),
    LineItem(
        "long_term_debt",
        "Long-term debt, noncurrent (excludes current maturities)",
        Statement.BALANCE_SHEET,
        ("us-gaap:LongTermDebtNoncurrent",),
        "USD",
        instant=True,
    ),
)

DERIVED_METRICS = (("ibi:gross_margin", "Gross margin"), ("ibi:free_cash_flow", "Free cash flow"))


class CellStatus(StrEnum):
    VALUE = "value"
    RESTATED = "restated"
    CONFLICTING = "conflicting"
    MISSING = "missing"
    UNVERIFIED_REVISION = "unverified_revision"


@dataclass(frozen=True, slots=True)
class Source:
    accession_number: str
    form_type: str | None
    tag: str
    value: Decimal
    known_available_at: datetime
    fact_id: int
    url: str
    availability_precision: str = "unknown"


@dataclass(frozen=True, slots=True)
class FilingNotice:
    accession_number: str
    form_type: str
    filing_date: date
    known_available_at: datetime
    items: str | None
    url: str
    availability_precision: str


@dataclass(frozen=True, slots=True)
class Cell:
    status: CellStatus
    value: Decimal | None = None
    source: Source | None = None
    original: Source | None = None
    """For RESTATED cells: the first filing's report of this value."""
    conflicting: tuple[Source, ...] = ()
    historical_conflicts: tuple[Source, ...] = ()
    evidence: tuple[Source, ...] = ()
    notices: tuple[FilingNotice, ...] = ()
    reason_code: str | None = None
    detail: str = ""


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
    result_id: int | None = None
    generation_id: int | None = None
    versions: results_store.VersionPair | None = None
    basis_accession: str | None = None
    effective_from: datetime | None = None
    revision_kind: str | None = None
    evidence: tuple[EvidenceRef, ...] = ()
    reason_code: str | None = None
    unit: str = ""
    notices: tuple[FilingNotice, ...] = ()


@dataclass(slots=True)
class Fundamentals:
    entity_id: str
    as_of: datetime
    statement_version: str
    years: list[FiscalYear]
    rows: dict[str, dict[date, Cell]] = field(default_factory=dict)
    derived: dict[str, dict[date, DerivedCell]] = field(default_factory=dict)
    non_reliance_coverage: str = NON_RELIANCE_COVERAGE

    @property
    def has_data(self) -> bool:
        return bool(self.years)


def filing_url(cik: int, accession: str, primary_document: str | None) -> str:
    folder = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}"
    return f"{folder}/{primary_document}" if primary_document else f"{folder}/{accession}-index.htm"


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _affected_notices(
    notices: tuple[FilingNotice, ...], basis_time: datetime, end: date
) -> tuple[FilingNotice, ...]:
    return tuple(
        n
        for n in notices
        if n.form_type in POLICY_V1.recast_forms
        and n.items is not None
        and POLICY_V1.non_reliance_item in [i.strip() for i in n.items.split(",")]
        and n.known_available_at >= basis_time
        and end < n.filing_date
    )


def _cell(
    item: LineItem,
    facts: list[FinancialDataPointRecord],
    source_of,
    notices: tuple[FilingNotice, ...] = (),
) -> Cell:
    """Resolve an exact period from already as-of-filtered facts and events."""
    if not facts:
        return Cell(CellStatus.MISSING)
    priority = {tag: i for i, tag in enumerate(item.tags)}

    def ordered(rows):
        return sorted(
            rows,
            key=lambda r: (
                _utc(r.known_available_at),
                r.accession_number or "",
                priority[r.metric_id],
                r.id,
            ),
        )

    def sources(rows):
        return tuple(source_of(r) for r in ordered(rows))

    grouped: dict[str, list[FinancialDataPointRecord]] = {}
    for fact in facts:
        grouped.setdefault(fact.accession_number or "", []).append(fact)
    annual = {
        accession: rows
        for accession, rows in grouped.items()
        if len({r.source_form_type for r in rows}) == 1 and rows[0].source_form_type in ANNUAL_FORMS
    }
    if not annual:
        return Cell(
            CellStatus.MISSING,
            evidence=sources(facts),
            detail="No eligible annual filing reports this item",
        )
    epochs = {a: max(_utc(r.known_available_at) for r in rows) for a, rows in grouped.items()}
    latest_time = max(epochs[a] for a in annual)
    latest_rows = [r for a, rows in annual.items() if epochs[a] == latest_time for r in rows]

    historical_epochs: dict[datetime, list[FinancialDataPointRecord]] = {}
    for accession, rows in grouped.items():
        if epochs[accession] < latest_time:
            historical_epochs.setdefault(epochs[accession], []).extend(rows)
    history = sources(
        [
            r
            for rows in historical_epochs.values()
            if len({Decimal(f.value) for f in rows}) > 1
            for r in rows
        ]
    )
    affected = _affected_notices(notices, latest_time, latest_rows[0].period_end_date)
    if len({Decimal(r.value) for r in latest_rows}) > 1:
        within_filing = any(
            epochs[a] == latest_time and len({Decimal(r.value) for r in rows}) > 1
            for a, rows in annual.items()
        )
        return Cell(
            CellStatus.CONFLICTING,
            conflicting=sources(latest_rows),
            historical_conflicts=history,
            notices=affected,
            reason_code=(
                UncertaintyReason.TAG_DISAGREEMENT.value
                if within_filing
                else UncertaintyReason.SIMULTANEOUS_DIVERGENCE.value
            ),
            detail="Latest annual filing epoch reports conflicting values",
        )

    # Choose a representative only AFTER every value at the latest annual epoch agrees.
    latest = min(latest_rows, key=lambda r: (priority[r.metric_id], r.accession_number or "", r.id))
    value = Decimal(latest.value)
    supporting = list(latest_rows)
    divergent = []
    for accession, rows in grouped.items():
        if epochs[accession] < latest_time or accession in annual:
            continue
        values = {Decimal(r.value) for r in rows}
        if values == {value}:
            supporting.extend(rows)
            continue
        divergent.append((epochs[accession], accession, rows, values))
    simultaneous = [r for when, _, rows, _ in divergent if when == latest_time for r in rows]
    later_epochs: dict[datetime, list[FinancialDataPointRecord]] = {}
    for accession, rows in grouped.items():
        if epochs[accession] > latest_time:
            later_epochs.setdefault(epochs[accession], []).extend(rows)
    simultaneous.extend(
        r
        for rows in later_epochs.values()
        if len({r.accession_number for r in rows}) > 1 and len({Decimal(r.value) for r in rows}) > 1
        for r in rows
    )
    if simultaneous:
        return Cell(
            CellStatus.CONFLICTING,
            source=source_of(latest),
            conflicting=sources(latest_rows + simultaneous),
            historical_conflicts=history,
            notices=affected,
            reason_code=UncertaintyReason.SIMULTANEOUS_DIVERGENCE.value,
            detail="Filings with the same availability timestamp disagree",
        )
    later_conflicts = [r for _, _, rows, values in divergent if len(values) > 1 for r in rows]
    if later_conflicts:
        return Cell(
            CellStatus.CONFLICTING,
            source=source_of(latest),
            conflicting=sources(latest_rows + later_conflicts),
            historical_conflicts=history,
            notices=affected,
            reason_code=UncertaintyReason.TAG_DISAGREEMENT.value,
            detail="A later filing reports conflicting values",
        )
    if divergent:
        _, _, rows, _ = min(divergent, key=lambda d: (d[0], d[1]))
        forms = {r.source_form_type for r in rows}
        tier = POLICY_V1.tier(next(iter(forms)) if len(forms) == 1 else None)
        code = {
            FormTier.RECAST: UncertaintyReason.UNCLASSIFIED_8K_DIVERGENCE,
            FormTier.PERIODIC: UncertaintyReason.PARTIAL_PERIODIC_DIVERGENCE,
            FormTier.OTHER: UncertaintyReason.UNCLASSIFIED_FORM_DIVERGENCE,
        }[tier]
        return Cell(
            CellStatus.UNVERIFIED_REVISION,
            source=source_of(latest),
            historical_conflicts=history,
            notices=affected,
            evidence=sources(supporting + [r for _, _, group, _ in divergent for r in group]),
            reason_code=code.value,
            detail="Later non-annual evidence diverges from the annual basis",
        )
    if affected:
        return Cell(
            CellStatus.UNVERIFIED_REVISION,
            source=source_of(latest),
            historical_conflicts=history,
            evidence=sources(supporting),
            notices=affected,
            reason_code=UncertaintyReason.NON_RELIANCE_DECLARED.value,
            detail="Item 4.02 declares non-reliance on the annual basis",
        )
    first_time = min(epochs[a] for a in annual)
    first_rows = [r for a, rows in annual.items() if epochs[a] == first_time for r in rows]
    original = None
    if len({Decimal(r.value) for r in first_rows}) == 1:
        first = min(
            first_rows, key=lambda r: (priority[r.metric_id], r.accession_number or "", r.id)
        )
        if Decimal(first.value) != value:
            original = source_of(first)
    return Cell(
        CellStatus.RESTATED if original else CellStatus.VALUE,
        value=value,
        source=source_of(latest),
        original=original,
        historical_conflicts=history,
        evidence=sources(supporting),
    )


def annual_fundamentals(
    session: Session,
    entity_id: str,
    cik: int,
    as_of: datetime | None = None,
    *,
    max_years: int = MAX_YEARS,
) -> Fundamentals:
    as_of = _utc(as_of) if as_of is not None else datetime.now(UTC)
    tags = sorted({t for item in LINE_ITEMS for t in item.tags})
    quarantined = results_store.quarantined_fact_ids()
    facts = (
        session.execute(
            select(FinancialDataPointRecord).where(
                FinancialDataPointRecord.entity_id == entity_id,
                FinancialDataPointRecord.metric_id.in_(tags),
                FinancialDataPointRecord.epistemic_label == "fact",
                FinancialDataPointRecord.known_available_at <= as_of,
                FinancialDataPointRecord.value.is_not(None),
                FinancialDataPointRecord.id.not_in(quarantined),
            )
        )
        .scalars()
        .all()
    )

    filings = (
        session.execute(
            select(FilingRecord).where(
                FilingRecord.entity_id == entity_id,
                FilingRecord.known_available_at <= as_of,
            )
        )
        .scalars()
        .all()
    )
    documents = {f.accession_number: f.primary_document for f in filings}
    notices = tuple(
        FilingNotice(
            accession_number=f.accession_number,
            form_type=f.form_type,
            filing_date=f.filing_date,
            known_available_at=_utc(f.known_available_at),
            items=f.items,
            url=filing_url(cik, f.accession_number, f.primary_document),
            availability_precision=f.availability_precision,
        )
        for f in sorted(filings, key=lambda f: (_utc(f.known_available_at), f.accession_number))
    )

    def source_of(f: FinancialDataPointRecord) -> Source:
        accession = f.accession_number or ""
        return Source(
            accession_number=accession,
            form_type=f.source_form_type,
            tag=f.metric_id,
            value=Decimal(f.value),
            known_available_at=_utc(f.known_available_at),
            fact_id=f.id,
            url=filing_url(cik, accession, documents.get(accession)),
            availability_precision=f.availability_precision or "unknown",
        )

    duration_items = [i for i in LINE_ITEMS if not i.instant]
    duration_tags = {t: i for i in duration_items for t in i.tags}
    years = sorted(
        {
            FiscalYear(f.start_date, f.period_end_date)
            for f in facts
            if f.start_date is not None
            and f.metric_id in duration_tags
            and f.source_form_type in ANNUAL_FORMS
            and f.unit == duration_tags[f.metric_id].unit
            and ANNUAL_DAYS[0] <= (f.period_end_date - f.start_date).days <= ANNUAL_DAYS[1]
        },
        key=lambda y: y.end,
        reverse=True,
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
                f
                for f in facts
                if f.metric_id in item.tags
                and f.unit == item.unit
                and f.period_end_date == y.end
                and ((f.start_date is None) if item.instant else (f.start_date == y.start))
            ]
            row[y.end] = _cell(item, matching, source_of, notices)
        result.rows[item.key] = row

    for metric_id, _ in DERIVED_METRICS:
        versions = results_store.VersionPair("1", "1")
        derived: dict[date, DerivedCell] = {}
        for y in years:
            answer = results_store.read_current(
                session, entity_id, metric_id, y.start, y.end, as_of, versions=versions
            )
            if isinstance(answer, results_store.ResolvedMetric):
                derived[y.end] = DerivedCell(
                    "value",
                    answer.value,
                    result_id=answer.result_id,
                    generation_id=answer.generation_id,
                    versions=answer.versions,
                    basis_accession=answer.basis_accession,
                    effective_from=answer.effective_from,
                    revision_kind=answer.revision_kind,
                    evidence=answer.evidence,
                    unit=POLICY_V1.metric(metric_id).unit,
                )
                # The stored v1 policy only treats strictly later notices as
                # non-reliance. Statement v2 also withholds values on a tie:
                # day-conservative timestamps cannot establish event ordering.
                # Retain the stored calculation's provenance without changing
                # the registered formula, policy or persisted result.
                basis_time = max(
                    (
                        _utc(e.known_available_at)
                        for e in answer.evidence
                        if e.relation == "input" and e.accession_number == answer.basis_accession
                    ),
                    default=_utc(answer.effective_from),
                )
                affected = _affected_notices(notices, basis_time, y.end)
                if affected:
                    derived[y.end] = replace(
                        derived[y.end],
                        status="UnverifiedRevision",
                        value=None,
                        reason_code=UncertaintyReason.NON_RELIANCE_DECLARED.value,
                        detail="Statement v2: item 4.02 at or after the calculation basis",
                        notices=affected,
                    )
            elif is_uncertain(answer):
                code = getattr(answer, "reason_code", None)
                evidence = getattr(answer, "evidence", ())
                input_times = [
                    _utc(e.known_available_at) for e in evidence if e.relation == "input"
                ]
                derived[y.end] = DerivedCell(
                    type(answer).__name__,
                    detail=answer.reason,
                    reason_code=code.value if code else None,
                    versions=versions,
                    evidence=evidence,
                    unit=POLICY_V1.metric(metric_id).unit,
                    notices=(
                        _affected_notices(notices, max(input_times), y.end) if input_times else ()
                    ),
                )
            else:  # pragma: no cover - readers only return the two shapes above
                derived[y.end] = DerivedCell("not_computed")
        result.derived[metric_id] = derived
    return result
