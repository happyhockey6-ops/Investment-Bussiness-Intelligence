"""Annual fundamentals read model and research page, on SQLite and PostgreSQL.

Synthetic filings for Apple's CIK (so the covered-company lookup resolves);
values are invented test data, not Apple's reported figures.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from ibi.api.app import create_app
from ibi.data_engine.sec_edgar.fixed_universe import FixedUniverseEntity
from ibi.data_engine.sec_edgar.ingest import ingest_entity
from ibi.db.base import Base
from ibi.db.models import EntityRecord, FilingRecord, FinancialDataPointRecord
from ibi.financial_engine import results_store as rs
from ibi.research.fundamentals import CellStatus, annual_fundamentals
from tests.integration.test_sec_edgar_ingestion import FakeConnector, _load

CIK, ENTITY = 320193, "CIK0000320193"
FY23 = (date(2022, 9, 25), date(2023, 9, 30))
FY24 = (date(2023, 10, 1), date(2024, 9, 28))
A1, A2, Q1, K8 = ("0000320193-23-000106", "0000320193-24-000123",
                  "0000320193-24-000069", "0000320193-24-000080")
T1 = datetime(2023, 11, 3, 14, 30, tzinfo=UTC)
T2 = datetime(2024, 11, 1, 14, 30, tzinfo=UTC)
TQ = datetime(2024, 8, 2, 14, 30, tzinfo=UTC)


@pytest.fixture(params=["sqlite", pytest.param("postgresql", marks=pytest.mark.integration)])
def engine(request, tmp_path, monkeypatch):
    admin = eng = None
    schema = "research_test_" + uuid4().hex
    try:
        if request.param == "postgresql":
            url = make_url(request.getfixturevalue("postgres_test_url"))
            admin = create_engine(url, connect_args={"connect_timeout": 3})
            with admin.begin() as c:
                c.execute(CreateSchema(schema))
            eng = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
        else:
            eng = create_engine(f"sqlite:///{tmp_path / 'research.db'}")
        Base.metadata.create_all(eng)
        yield eng
    finally:
        if eng is not None:
            eng.dispose()
        if admin is not None:
            with admin.begin() as c:
                c.execute(DropSchema(schema, cascade=True))
            admin.dispose()


def fact(tag, value, period, accession, form, available, *, instant=False, unit="USD"):
    start, end = period
    return FinancialDataPointRecord(
        entity_id=ENTITY, metric_id=tag, unit=unit, start_date=None if instant else start,
        period_end_date=end, value=Decimal(value), epistemic_label="fact",
        accession_number=accession, known_available_at=available,
        availability_precision="acceptance_timestamp", source_form_type=form,
    )


REV = "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"


@pytest.fixture
def seeded(engine):
    with Session(engine) as s, s.begin():
        s.add(EntityRecord(entity_id=ENTITY, kind="company", canonical_name="Apple Inc."))
        s.flush()
        for accession, form, filed, available, doc in (
            (A1, "10-K", date(2023, 11, 3), T1, "aapl-20230930.htm"),
            (A2, "10-K", date(2024, 11, 1), T2, "aapl-20240928.htm"),
        ):
            s.add(FilingRecord(
                entity_id=ENTITY, source="sec_edgar", accession_number=accession,
                form_type=form, filing_date=filed, primary_document=doc,
                known_available_at=available, availability_precision="acceptance_timestamp",
                items="",
            ))
        s.add_all([
            # FY2023 as first reported.
            fact(REV, "383285000000", FY23, A1, "10-K", T1),
            fact("us-gaap:NetIncomeLoss", "96995000000", FY23, A1, "10-K", T1),
            fact("us-gaap:Assets", "352583000000", FY23, A1, "10-K", T1, instant=True),
            # FY2024 10-K: new year, comparatives (one restated), agreeing tags.
            fact(REV, "391035000000", FY24, A2, "10-K", T2),
            fact("us-gaap:Revenues", "391035000000", FY24, A2, "10-K", T2),
            fact(REV, "383285000000", FY23, A2, "10-K", T2),
            fact("us-gaap:NetIncomeLoss", "97000000000", FY23, A2, "10-K", T2),
            fact("us-gaap:CostOfGoodsAndServicesSold", "210352000000", FY24, A2, "10-K", T2),
            fact("us-gaap:CostOfRevenue", "210000000000", FY24, A2, "10-K", T2),
            fact("us-gaap:NetCashProvidedByUsedInOperatingActivities", "118254000000", FY24,
                 A2, "10-K", T2),
            fact("us-gaap:PaymentsToAcquirePropertyPlantAndEquipment", "9447000000", FY24,
                 A2, "10-K", T2),
            fact("us-gaap:EarningsPerShareDiluted", "6.08", FY24, A2, "10-K", T2,
                 unit="USD/shares"),
            fact("us-gaap:Assets", "364980000000", FY24, A2, "10-K", T2, instant=True),
            # Non-annual forms never feed the annual page.
            fact(REV, "1", FY24, Q1, "10-Q", TQ),
            fact("us-gaap:NetIncomeLoss", "2", FY24, K8, "8-K", TQ),
        ])
    return engine


def build(engine, as_of=None):
    with Session(engine) as s:
        return annual_fundamentals(s, ENTITY, CIK, as_of)


def test_as_currently_reported_with_restatements_and_provenance(seeded):
    f = build(seeded)
    assert [y.label for y in f.years] == ["FY2024", "FY2023"]
    rev24, rev23 = f.rows["revenue"][FY24[1]], f.rows["revenue"][FY23[1]]
    assert (rev24.status, rev24.value) == (CellStatus.VALUE, Decimal("391035000000"))
    assert rev24.source.tag == REV  # first listed tag wins when tags agree
    assert rev24.source.url == ("https://www.sec.gov/Archives/edgar/data/320193/"
                                "000032019324000123/aapl-20240928.htm")
    assert (rev23.status, rev23.source.accession_number) == (CellStatus.VALUE, A2)

    ni23 = f.rows["net_income"][FY23[1]]
    assert ni23.status is CellStatus.RESTATED
    assert ni23.value == Decimal("97000000000") and ni23.source.accession_number == A2
    assert ni23.original.value == Decimal("96995000000") and ni23.original.accession_number == A1

    cost = f.rows["cost_of_revenue"][FY24[1]]
    assert cost.status is CellStatus.CONFLICTING and cost.value is None
    assert {s.value for s in cost.conflicting} == {Decimal("210352000000"),
                                                   Decimal("210000000000")}

    assert f.rows["net_income"][FY24[1]].status is CellStatus.MISSING  # 8-K ignored
    assert f.rows["total_assets"][FY24[1]].value == Decimal("364980000000")
    assert f.rows["eps_diluted"][FY24[1]].value == Decimal("6.08")


def test_point_in_time_view_hides_later_filings(seeded):
    f = build(seeded, datetime(2024, 1, 1, tzinfo=UTC))
    assert [y.label for y in f.years] == ["FY2023"]
    ni23 = f.rows["net_income"][FY23[1]]
    assert (ni23.status, ni23.value) == (CellStatus.VALUE, Decimal("96995000000"))
    assert ni23.source.accession_number == A1


def test_quarantined_fact_is_excluded(seeded):
    with Session(seeded) as s, s.begin():
        assets = s.query(FinancialDataPointRecord).filter_by(
            metric_id="us-gaap:Assets", accession_number=A2).one()
        rs.quarantine_fact(s, assets.id, "test: bad extraction")
    assert build(seeded).rows["total_assets"][FY24[1]].status is CellStatus.MISSING


def test_derived_metrics_are_read_from_results_store(seeded):
    f = build(seeded)
    assert f.derived["ibi:free_cash_flow"][FY24[1]].status == "Unknown"  # never built
    with Session(seeded) as s, s.begin():
        for metric in ("ibi:free_cash_flow", "ibi:gross_margin"):
            rs.build_generation(s, ENTITY, metric, rs.VersionPair("1", "1"))
    f = build(seeded)
    fcf = f.derived["ibi:free_cash_flow"][FY24[1]]
    assert (fcf.status, fcf.value) == ("value", Decimal("108807000000"))
    gm = f.derived["ibi:gross_margin"][FY24[1]]
    assert gm.status == "ConflictingEvidence" and gm.value is None


def client(engine):
    return TestClient(create_app(sessionmaker(bind=engine)))


def test_json_api(seeded):
    c = client(seeded)
    assert c.get("/api/companies", params={"q": "app"}).json()[0]["ticker"] == "AAPL"
    body = c.get("/api/companies/aapl/fundamentals").json()
    assert body["company"]["cik"] == CIK and body["statement_version"] == "2"
    assert [y["label"] for y in body["fiscal_years"]] == ["FY2024", "FY2023"]
    items = {i["key"]: i for i in body["line_items"]}
    ni23 = items["net_income"]["values"]["2023-09-30"]
    assert ni23["status"] == "restated" and ni23["value"] == "97000000000"
    assert ni23["original"]["accession_number"] == A1
    assert c.get("/api/companies/ZZZZ/fundamentals").status_code == 404
    assert c.get("/api/companies/AAPL/fundamentals",
                 params={"as_of": "2024-01-01T00:00:00"}).status_code == 422


def test_html_pages(seeded):
    c = client(seeded)
    page = c.get("/companies/AAPL")
    assert page.status_code == 200 and "Apple Inc." in page.text
    assert "width=device-width" in page.text and "noindex" in page.text
    assert "000032019324000123/aapl-20240928.htm" in page.text
    assert "391,035" in page.text and "6.08" in page.text
    assert "class=restated" in page.text and "class=conflict" in page.text
    home = c.get("/", params={"q": "<script>alert(1)</script>"})
    assert "<script>alert" not in home.text and "&lt;script&gt;" in home.text
    assert "/companies/AAPL" in c.get("/", params={"q": "aapl"}).text
    assert c.get("/companies/NOPE").status_code == 404


def test_routes_never_commit(seeded):
    c = client(seeded)
    with Session(seeded) as s:
        before = s.query(FinancialDataPointRecord).count()
    for path in ("/", "/companies/AAPL", "/api/companies/AAPL/fundamentals"):
        assert c.get(path).status_code == 200
    with Session(seeded) as s:
        assert s.query(FinancialDataPointRecord).count() == before


def test_real_fixture_ingest_renders(engine, tmp_path):
    entity = FixedUniverseEntity(cik=CIK, entity_id=ENTITY, canonical_name="Apple Inc.")
    with Session(engine) as s, s.begin():
        ingest_entity(s, FakeConnector(_load("aapl_submissions.json"),
                                       _load("aapl_companyfacts.json")), entity, Path(tmp_path))
    f = build(engine)
    assert f.has_data
    assert any(c.status is CellStatus.VALUE for c in f.rows["total_assets"].values())
    assert client(engine).get("/companies/AAPL").status_code == 200


def test_reconciliation_reports_matches_and_mismatches(seeded):
    from ibi.validation.sec_reconciliation import reconcile

    expected = {"companies": {"AAPL": {
        "filing": "synthetic", "verified": True, "fiscal_year_end": "2024-09-28",
        "values": {"revenue": "391035000000", "total_assets": "1",
                   "cost_of_revenue": "210352000000", "net_income": "5"},
    }}}
    with Session(seeded) as s:
        lines, ok = reconcile(s, expected)
    text = "\n".join(lines)
    assert not ok
    assert "ok   revenue: 391035000000" in text
    assert "FAIL total_assets: expected 1, got 364980000000" in text
    assert "FAIL cost_of_revenue: expected 210352000000, page shows conflicting" in text
    assert "FAIL net_income: expected 5, page shows missing" in text
