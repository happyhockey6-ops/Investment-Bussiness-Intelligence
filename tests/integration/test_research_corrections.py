"""Regressions for all six findings in the review of PR #6 at 0427e65."""

import copy
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from html.parser import HTMLParser
from types import SimpleNamespace

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from ibi.api import render
from ibi.core.types import EvidenceRef
from ibi.data_engine.sec_edgar.fixed_universe import FIXED_UNIVERSE
from ibi.db.models import FilingRecord, FinancialDataPointRecord
from ibi.financial_engine import results_store as rs
from ibi.research.fundamentals import (
    LINE_ITEMS,
    Cell,
    CellStatus,
    DerivedCell,
    FiscalYear,
    Fundamentals,
    Source,
)
from ibi.validation import sec_reconciliation as validation
from tests.integration.test_research_fundamentals import (
    A1,
    A2,
    ENTITY,
    FY23,
    FY24,
    REV,
    T1,
    T2,
    build,
    client,
    fact,
)

# Explicit re-exports register the original parametrized fixtures in this module.
from tests.integration.test_research_fundamentals import engine as engine
from tests.integration.test_research_fundamentals import seeded as seeded

LATER = T2 + timedelta(days=30)
CORRECTION = T2 + timedelta(days=60)


def notice(engine, when, *, form="8-K", items="4.02", accession="notice"):
    with Session(engine) as s, s.begin():
        s.add(
            FilingRecord(
                entity_id=ENTITY,
                source="sec_edgar",
                accession_number=accession,
                form_type=form,
                filing_date=when.date(),
                known_available_at=when,
                items=items,
                availability_precision="day_conservative",
                primary_document="notice.htm",
            )
        )


@pytest.mark.parametrize("form", ["8-K", "8-K/A"])
def test_non_reliance_is_point_in_time_and_covers_duration_and_instant(seeded, form):
    notice(seeded, LATER, form=form)
    before = build(seeded, LATER - timedelta(seconds=1))
    assert before.rows["revenue"][FY24[1]].status is CellStatus.VALUE
    current = build(seeded, LATER)
    for key in ("revenue", "total_assets"):
        cell = current.rows[key][FY24[1]]
        assert cell.status is CellStatus.UNVERIFIED_REVISION and cell.value is None
        assert cell.reason_code == "non_reliance_declared"
        assert cell.source.accession_number == A2
        assert cell.notices[0].accession_number == "notice"
        assert cell.notices[0].availability_precision == "day_conservative"
    api = client(seeded).get(
        "/api/companies/AAPL/fundamentals", params={"as_of": LATER.isoformat()}
    )
    revenue = api.json()["line_items"][0]["values"][str(FY24[1])]
    assert revenue["value"] is None and revenue["notices"][0]["items"] == "4.02"


@pytest.mark.parametrize("form", ["10-K", "10-K/A"])
def test_later_eligible_annual_basis_supersedes_non_reliance(seeded, form):
    notice(seeded, LATER)
    with Session(seeded) as s, s.begin():
        s.add(fact(REV, "400000000000", FY24, "corrected", form, CORRECTION))
        s.add(
            fact(
                "us-gaap:Assets", "370000000000", FY24, "corrected", form, CORRECTION, instant=True
            )
        )
    f = build(seeded, CORRECTION)
    for key in ("revenue", "total_assets"):
        cell = f.rows[key][FY24[1]]
        assert cell.status is CellStatus.RESTATED
        assert cell.source.accession_number == "corrected" and not cell.notices
    assert f.rows["revenue"][FY24[1]].value == Decimal("400000000000")


def test_equal_timestamp_notice_cannot_be_ordered_after_or_before_basis(seeded):
    notice(seeded, T2)
    cell = build(seeded, T2).rows["revenue"][FY24[1]]
    assert cell.status is CellStatus.UNVERIFIED_REVISION
    assert cell.value is None and cell.reason_code == "non_reliance_declared"


def test_unrelated_or_older_notices_do_not_exclude_annual_filings(seeded):
    notice(seeded, LATER, items="2.02", accession="earnings")
    notice(seeded, T1, accession="old-notice")
    f = build(seeded, LATER)
    assert f.rows["revenue"][FY24[1]].value == Decimal("391035000000")
    assert f.rows["total_assets"][FY24[1]].value == Decimal("364980000000")


@pytest.mark.parametrize(
    ("form", "code"),
    [
        ("8-K", "unclassified_8k_divergence"),
        ("8-K/A", "unclassified_8k_divergence"),
        ("10-Q", "partial_periodic_divergence"),
        (None, "unclassified_form_divergence"),
    ],
)
def test_later_divergent_evidence_is_not_promoted_to_an_annual_basis(seeded, form, code):
    with Session(seeded) as s, s.begin():
        s.add(fact(REV, "1", FY24, "divergent", form, LATER))
        s.add(fact("us-gaap:Assets", "2", FY24, "divergent", form, LATER, instant=True))
    before = build(seeded, T2)
    assert before.rows["revenue"][FY24[1]].status is CellStatus.VALUE
    for key in ("revenue", "total_assets"):
        cell = build(seeded, LATER).rows[key][FY24[1]]
        assert cell.status is CellStatus.UNVERIFIED_REVISION and cell.value is None
        assert cell.reason_code == code and cell.source.accession_number == A2
        assert any(e.accession_number == "divergent" for e in cell.evidence)


def test_agreeing_later_nonannual_fact_only_corroborates(seeded):
    with Session(seeded) as s, s.begin():
        s.add(fact(REV, "391035000000", FY24, "corroboration", "8-K", LATER))
    cell = build(seeded, LATER).rows["revenue"][FY24[1]]
    assert cell.status is CellStatus.VALUE and cell.source.accession_number == A2
    assert {e.accession_number for e in cell.evidence} == {A2, "corroboration"}


@pytest.mark.parametrize("accession", ["0000000000-00-000001", "9999999999-99-999999"])
def test_exact_review_case_simultaneous_100_120_is_conflicting(seeded, accession):
    with Session(seeded) as s, s.begin():
        s.execute(
            delete(FinancialDataPointRecord).where(
                FinancialDataPointRecord.metric_id.in_([REV, "us-gaap:Revenues"])
            )
        )
        s.add_all(
            [
                fact(REV, "100", FY24, A2, "10-K", T2),
                fact(REV, "120", FY24, accession, "10-K/A", T2),
            ]
        )
    cell = build(seeded, T2).rows["revenue"][FY24[1]]
    assert cell.status is CellStatus.CONFLICTING and cell.value is None
    assert cell.reason_code == "simultaneous_divergence"
    assert {e.value for e in cell.conflicting} == {Decimal(100), Decimal(120)}


def test_simultaneous_nonannual_divergence_and_agreeing_annual_ties(seeded):
    with Session(seeded) as s, s.begin():
        s.add(fact(REV, "391035000000", FY24, "annual-tie", "10-K/A", T2))
    assert build(seeded, T2).rows["revenue"][FY24[1]].status is CellStatus.VALUE
    with Session(seeded) as s, s.begin():
        s.add(fact(REV, "120", FY24, "nonannual-tie", "8-K", T2))
    cell = build(seeded, T2).rows["revenue"][FY24[1]]
    assert cell.status is CellStatus.CONFLICTING and cell.reason_code == "simultaneous_divergence"


@pytest.mark.parametrize("other_value", ["391035000000", "130"])
def test_later_simultaneous_nonannual_conflict_is_not_accession_ordered(seeded, other_value):
    with Session(seeded) as s, s.begin():
        s.add(fact(REV, "120", FY24, "later-a", "8-K", LATER))
        s.add(fact(REV, other_value, FY24, "later-z", "8-K/A", LATER))
    cell = build(seeded, LATER).rows["revenue"][FY24[1]]
    assert cell.status is CellStatus.CONFLICTING and cell.value is None
    assert cell.reason_code == "simultaneous_divergence"
    assert {"later-a", "later-z"}.issubset(e.accession_number for e in cell.conflicting)
    with Session(seeded) as s, s.begin():
        s.add(fact(REV, "400000000000", FY24, "new-annual", "10-K", CORRECTION))
    cell = build(seeded, CORRECTION).rows["revenue"][FY24[1]]
    assert cell.value == Decimal("400000000000")
    assert {"later-a", "later-z"}.issubset(e.accession_number for e in cell.historical_conflicts)


def test_exact_review_case_old_100_110_conflict_does_not_hide_new_120(seeded):
    with Session(seeded) as s, s.begin():
        s.execute(
            delete(FinancialDataPointRecord).where(
                FinancialDataPointRecord.metric_id.in_([REV, "us-gaap:Revenues"])
            )
        )
        s.add_all(
            [
                fact(REV, "100", FY24, A1, "10-K", T1),
                fact("us-gaap:Revenues", "110", FY24, A1, "10-K", T1),
                fact(REV, "120", FY24, A2, "10-K", T2),
                fact("us-gaap:Revenues", "120", FY24, A2, "10-K", T2),
            ]
        )
    assert build(seeded, T1).rows["revenue"][FY24[1]].status is CellStatus.CONFLICTING
    cell = build(seeded, T2).rows["revenue"][FY24[1]]
    assert (cell.status, cell.value) == (CellStatus.VALUE, Decimal(120))
    assert cell.original is None  # the ambiguous first epoch cannot supply an original
    assert {e.value for e in cell.historical_conflicts} == {Decimal(100), Decimal(110)}
    assert cell.source.accession_number == A2
    api_cell = client(seeded).get("/api/companies/AAPL/fundamentals").json()["line_items"][0]
    assert len(api_cell["values"][str(FY24[1])]["historical_conflicts"]) == 2


def test_latest_conflict_never_falls_back_to_an_older_good_value(seeded):
    with Session(seeded) as s, s.begin():
        s.add(fact("us-gaap:Revenues", "1", FY23, A2, "10-K", T2))
    cell = build(seeded, T2).rows["revenue"][FY23[1]]
    assert cell.status is CellStatus.CONFLICTING and cell.value is None


def test_new_annual_filing_supersedes_simultaneous_historical_conflict(seeded):
    with Session(seeded) as s, s.begin():
        s.add(fact(REV, "1", FY24, "tied-old-amendment", "10-K/A", T2))
        s.add(fact(REV, "400000000000", FY24, "new-annual", "10-K", LATER))
    assert build(seeded, T2).rows["revenue"][FY24[1]].status is CellStatus.CONFLICTING
    cell = build(seeded, LATER).rows["revenue"][FY24[1]]
    assert (cell.status, cell.value) == (CellStatus.VALUE, Decimal("400000000000"))
    assert {e.accession_number for e in cell.historical_conflicts} == {A2, "tied-old-amendment"}


def test_new_annual_filing_supersedes_older_nonannual_divergence(seeded):
    with Session(seeded) as s, s.begin():
        s.add(fact(REV, "1", FY24, "divergent", "8-K", LATER))
        s.add(fact(REV, "400000000000", FY24, "new-annual", "10-K/A", CORRECTION))
    assert build(seeded, LATER).rows["revenue"][FY24[1]].value is None
    cell = build(seeded, CORRECTION).rows["revenue"][FY24[1]]
    assert (cell.status, cell.value) == (CellStatus.RESTATED, Decimal("400000000000"))


def test_debt_is_noncurrent_only_across_periods(seeded):
    with Session(seeded) as s, s.begin():
        s.add_all(
            [
                fact("us-gaap:LongTermDebtNoncurrent", "100", FY24, A2, "10-K", T2, instant=True),
                fact("us-gaap:LongTermDebt", "120", FY24, A2, "10-K", T2, instant=True),
                fact("us-gaap:LongTermDebt", "110", FY23, A1, "10-K", T1, instant=True),
            ]
        )
    row = build(seeded).rows["long_term_debt"]
    assert (row[FY24[1]].status, row[FY24[1]].value) == (CellStatus.VALUE, Decimal(100))
    assert row[FY23[1]].status is CellStatus.MISSING and row[FY23[1]].value is None
    item = next(i for i in LINE_ITEMS if i.key == "long_term_debt")
    assert item.tags == ("us-gaap:LongTermDebtNoncurrent",)
    assert "excludes current maturities" in item.label


def test_resolved_metric_provenance_survives_read_model_and_json(seeded):
    with Session(seeded) as s, s.begin():
        generation = rs.build_generation(s, ENTITY, "ibi:free_cash_flow", rs.VersionPair("1", "1"))
    cell = build(seeded).derived["ibi:free_cash_flow"][FY24[1]]
    with Session(seeded) as s:
        answer = rs.read_current(
            s,
            ENTITY,
            "ibi:free_cash_flow",
            *FY24,
            datetime.now(UTC),
            versions=rs.VersionPair("1", "1"),
        )
    assert cell.result_id == answer.result_id
    assert cell.generation_id == answer.generation_id == generation.generation_id
    assert cell.versions == answer.versions
    assert cell.basis_accession == answer.basis_accession == A2
    assert cell.effective_from == answer.effective_from
    assert cell.revision_kind == answer.revision_kind
    assert cell.evidence == answer.evidence and len(cell.evidence) == 2
    body = client(seeded).get("/api/companies/AAPL/fundamentals").json()
    derived = body["derived"]["ibi:free_cash_flow"][str(FY24[1])]
    assert derived["result_id"] == answer.result_id
    assert derived["generation_id"] == answer.generation_id
    assert derived["versions"] == {"calculation_version": "1", "selection_policy_version": "1"}
    assert derived["effective_from"] == answer.effective_from.isoformat()
    assert derived["basis_accession"] == A2 and derived["unit"] == "USD"
    assert {e["role"] for e in derived["evidence"]} == {
        "operating_cash_flow",
        "capital_expenditures",
    }
    assert all(isinstance(e["value"], str) and e["fact_id"] for e in derived["evidence"])


def test_tied_non_reliance_withholds_calculation_and_retains_provenance(seeded):
    notice(seeded, T2)
    with Session(seeded) as s, s.begin():
        generation = rs.build_generation(s, ENTITY, "ibi:free_cash_flow", rs.VersionPair("1", "1"))
    cell = build(seeded, T2).derived["ibi:free_cash_flow"][FY24[1]]
    assert cell.status == "UnverifiedRevision" and cell.value is None
    assert cell.reason_code == "non_reliance_declared"
    assert cell.result_id and cell.generation_id == generation.generation_id
    assert cell.versions == rs.VersionPair("1", "1") and cell.basis_accession == A2
    assert cell.evidence and cell.notices[0].accession_number == "notice"
    body = (
        client(seeded)
        .get("/api/companies/AAPL/fundamentals", params={"as_of": T2.isoformat()})
        .json()["derived"]["ibi:free_cash_flow"][str(FY24[1])]
    )
    assert body["value"] is None and body["notices"][0]["items"] == "4.02"
    assert body["result_id"] == cell.result_id and body["evidence"]
    html = render._derived_html(cell, "ibi:free_cash_flow", 320193)
    assert "Non-reliance notice" in html and "Notice filing" in html


def test_uncertain_calculation_retains_reason_versions_inputs_and_notice(seeded):
    notice(seeded, LATER)
    with Session(seeded) as s, s.begin():
        rs.build_generation(s, ENTITY, "ibi:free_cash_flow", rs.VersionPair("1", "1"))
    with Session(seeded) as s:
        answer = rs.read_current(
            s, ENTITY, "ibi:free_cash_flow", *FY24, LATER, versions=rs.VersionPair("1", "1")
        )
    cell = build(seeded, LATER).derived["ibi:free_cash_flow"][FY24[1]]
    assert cell.status == "UnverifiedRevision" and cell.value is None
    assert cell.detail == answer.reason and cell.reason_code == "non_reliance_declared"
    assert cell.versions == rs.VersionPair("1", "1")
    assert cell.evidence == answer.evidence and cell.notices[0].items == "4.02"


def test_calculation_revision_kind_and_superseded_inputs_reach_api(seeded):
    with Session(seeded) as s, s.begin():
        s.add_all(
            [
                fact(
                    "us-gaap:NetCashProvidedByUsedInOperatingActivities",
                    "119254000000",
                    FY24,
                    "amendment",
                    "10-K/A",
                    LATER,
                ),
                fact(
                    "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment",
                    "9447000000",
                    FY24,
                    "amendment",
                    "10-K/A",
                    LATER,
                ),
            ]
        )
        s.flush()
        rs.build_generation(s, ENTITY, "ibi:free_cash_flow", rs.VersionPair("1", "1"))
    body = client(seeded).get("/api/companies/AAPL/fundamentals").json()
    cell = body["derived"]["ibi:free_cash_flow"][str(FY24[1])]
    assert cell["status"] == "value" and cell["revision_kind"] == "amendment"
    assert cell["basis_accession"] == "amendment"
    assert any(
        e["relation"] == "superseded_basis" and e["accession_number"] == A2
        for e in cell["evidence"]
    )


class DetailsParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.summaries = 0
        self.links = []
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag == "details":
            self.depth += 1
        if self.depth and tag == "summary":
            self.summaries += 1
        if self.depth and tag == "a":
            self.links.append(dict(attrs).get("href"))

    def handle_endtag(self, tag):
        if tag == "details":
            self.depth -= 1

    def handle_data(self, data):
        if self.depth:
            self.text.append(data)


def test_touch_keyboard_details_include_original_conflict_and_calculation_evidence(seeded):
    with Session(seeded) as s, s.begin():
        rs.build_generation(s, ENTITY, "ibi:free_cash_flow", rs.VersionPair("1", "1"))
    page = client(seeded).get("/companies/AAPL").text
    details = DetailsParser()
    details.feed(page)
    assert details.summaries > 0 and details.depth == 0
    text = " ".join(details.text)
    assert "Originally reported" in text and "Conflicting evidence" in text
    assert "formula 1, policy 1" in text and "generation" in text
    assert "operating_cash_flow / input" in text
    assert any(A1.replace("-", "") in link for link in details.links)
    assert any(A2.replace("-", "") in link for link in details.links)
    assert "Free cash flow<span class='muted small unit'>USD millions</span>" in page


def test_uncertainty_details_and_evidence_are_escaped(seeded):
    source = Source("<script>", "10-K", "<img>", Decimal(1), T2, 1, "https://www.sec.gov/'x")
    cell = Cell(CellStatus.UNVERIFIED_REVISION, source=source, evidence=(source,), detail="<svg>")
    html = render._cell_html(cell, "USD")
    assert "<script>" not in html and "<img>" not in html and "<svg>" not in html
    assert "&lt;script&gt;" in html and "&lt;svg&gt;" in html
    assert "unverified revision" in html and "<details>" in html


@pytest.fixture
def reconciliation_case(monkeypatch):
    source = Source(A2, "10-K", REV, Decimal(100), T2, 1, "https://www.sec.gov/")
    f = Fundamentals(ENTITY, T2, "2", [FiscalYear(*FY24)])
    f.rows = {
        key: {FY24[1]: Cell(CellStatus.VALUE, Decimal(100), source)}
        for key in validation.REQUIRED_REFERENCE_ITEMS
    }
    evidence = (EvidenceRef(1, "input", "input", REV, A2, "10-K", Decimal(100), T2),)
    f.derived = {
        metric: {
            FY24[1]: DerivedCell(
                "value",
                value,
                result_id=1,
                generation_id=1,
                versions=rs.VersionPair("1", "1"),
                basis_accession=A2,
                effective_from=T2,
                evidence=evidence,
            )
        }
        for metric, value in (
            ("ibi:gross_margin", Decimal("0.25")),
            ("ibi:free_cash_flow", Decimal(25)),
        )
    }
    ref = {
        "filing": "synthetic unit-test reference",
        "verified": True,
        "fiscal_year_end": str(FY24[1]),
        "values": {key: "100" for key in validation.REQUIRED_REFERENCE_ITEMS},
        "derived": {"ibi:gross_margin": "0.25", "ibi:free_cash_flow": "25"},
    }
    expected = {"companies": {e.ticker: copy.deepcopy(ref) for e in FIXED_UNIVERSE}}
    monkeypatch.setattr(validation, "annual_fundamentals", lambda *args: f)
    return SimpleNamespace(f=f, expected=expected)


def test_reconciliation_pass_requires_verified_complete_values_and_provenance(reconciliation_case):
    lines, ok = validation.reconcile(None, reconciliation_case.expected)
    assert ok and "FAIL" not in "\n".join(lines)
    assert "ok   ibi:gross_margin: 0.25" in "\n".join(lines)
    assert "ok   ibi:free_cash_flow: 25" in "\n".join(lines)


def test_exact_review_case_no_data_and_no_references_cannot_pass(reconciliation_case):
    f = reconciliation_case.f
    f.years, f.rows, f.derived = [], {}, {}
    lines, ok = validation.reconcile(None, {"companies": {}})
    assert not ok
    assert "FAIL company: no annual data" in "\n".join(lines)
    assert "FAIL company: missing required reference" in "\n".join(lines)


@pytest.mark.parametrize(
    "status",
    [
        "Unknown",
        "InsufficientEvidence",
        "ConflictingEvidence",
        "UnverifiedRevision",
        "IncompatibleBasis",
        "missing",
        "null",
    ],
)
def test_matching_facts_with_unavailable_derived_metric_cannot_pass(reconciliation_case, status):
    f = reconciliation_case.f
    if status == "missing":
        f.derived.pop("ibi:gross_margin")
    else:
        f.derived["ibi:gross_margin"][FY24[1]] = DerivedCell(
            "value" if status == "null" else status
        )
    lines, ok = validation.reconcile(None, reconciliation_case.expected)
    assert not ok
    assert "ok   revenue: 100" in "\n".join(lines)
    assert "FAIL ibi:gross_margin: required metric unavailable" in "\n".join(lines)


@pytest.mark.parametrize(
    "defect",
    [
        "company",
        "empty_values",
        "reported_value",
        "derived_reference",
        "unverified",
        "wrong_derived_value",
        "invalid_number",
    ],
)
def test_incomplete_or_unverified_reference_cannot_pass(reconciliation_case, defect):
    ref = reconciliation_case.expected["companies"]["AAPL"]
    if defect == "company":
        del reconciliation_case.expected["companies"]["AAPL"]
    elif defect == "empty_values":
        ref["values"] = {}
    elif defect == "reported_value":
        del ref["values"]["revenue"]
    elif defect == "derived_reference":
        del ref["derived"]["ibi:free_cash_flow"]
    elif defect == "unverified":
        ref["verified"] = False
    elif defect == "wrong_derived_value":
        ref["derived"]["ibi:free_cash_flow"] = "26"
    else:
        ref["values"]["revenue"] = "NaN"
    lines, ok = validation.reconcile(None, reconciliation_case.expected)
    assert not ok and "FAIL" in "\n".join(lines)


def test_reconciliation_rejects_calculation_without_provenance(reconciliation_case):
    reconciliation_case.f.derived["ibi:free_cash_flow"][FY24[1]] = DerivedCell("value", Decimal(25))
    lines, ok = validation.reconcile(None, reconciliation_case.expected)
    assert not ok
    assert "FAIL ibi:free_cash_flow: calculation provenance is incomplete" in "\n".join(lines)


def test_reconciliation_rejects_unverified_cell_even_when_candidate_matches(reconciliation_case):
    old = reconciliation_case.f.rows["revenue"][FY24[1]]
    reconciliation_case.f.rows["revenue"][FY24[1]] = Cell(
        CellStatus.UNVERIFIED_REVISION, source=old.source, reason_code="non_reliance_declared"
    )
    lines, ok = validation.reconcile(None, reconciliation_case.expected)
    assert not ok
    assert "FAIL revenue: expected 100, page shows unverified_revision" in "\n".join(lines)


def test_shipped_references_remain_explicitly_unverified():
    expected = validation.load_expected()
    for ref in expected["companies"].values():
        assert ref["verified"] is False
        assert set(validation.REQUIRED_REFERENCE_ITEMS).issubset(ref["values"])
        assert set(validation.REQUIRED_DERIVED_METRICS).issubset(ref["derived"])


@pytest.mark.parametrize("case", ["complete", "no_references", "missing_metric", "ingest_failure"])
def test_cli_exit_and_pass_banner_require_successful_validation(
    reconciliation_case, monkeypatch, capsys, case
):
    # Exercise the CLI's final gate with no network, database or migration IO.
    expected = reconciliation_case.expected
    if case == "no_references":
        expected = {"companies": {}}
    elif case == "missing_metric":
        reconciliation_case.f.derived.pop("ibi:free_cash_flow")
    results = []
    if case == "ingest_failure":
        results = [
            SimpleNamespace(
                entity_id=ENTITY,
                status="failed",
                facts_written=0,
                facts_skipped_duplicate=0,
                error_type="DatabaseError",
                error="synthetic failure",
            )
        ]
    monkeypatch.setenv("IBI_SEC_USER_AGENT", "Trusty regression tests tests@example.com")
    monkeypatch.setattr(validation.command, "upgrade", lambda *args: None)
    monkeypatch.setattr(validation, "create_engine", lambda *args: object())
    monkeypatch.setattr(validation, "sessionmaker", lambda **kwargs: None)
    monkeypatch.setattr(validation.db, "_SessionFactory", None)
    session = SimpleNamespace(begin=lambda: nullcontext())
    monkeypatch.setattr(validation, "Session", lambda *args: nullcontext(session))
    monkeypatch.setattr(
        validation.ingest, "run_ingestion", lambda *args: SimpleNamespace(results=results)
    )
    outcome = SimpleNamespace(status="unchanged", rows_written=0)
    monkeypatch.setattr(validation.rs, "build_generation", lambda *args: outcome)
    monkeypatch.setattr(validation, "load_expected", lambda: expected)
    code = validation.main(["--database-url", "postgresql+psycopg://localhost/isolated_test"])
    output = capsys.readouterr().out
    assert code == (0 if case == "complete" else 1)
    assert ("RESULT: PASS" in output) is (case == "complete")
    assert ("RESULT: FAIL" in output) is (case != "complete")
