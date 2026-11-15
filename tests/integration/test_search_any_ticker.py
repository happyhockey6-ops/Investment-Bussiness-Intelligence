"""Directory/research states on SQLite and PostgreSQL, with no external HTTP."""

import json
import shlex
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.orm import Session, sessionmaker

from ibi.api.app import _jsonable, create_app
from ibi.data_engine.sec_edgar import ingest, listings
from ibi.data_engine.sec_edgar.client import SecHttpClient
from ibi.data_engine.sec_edgar.fixed_universe import FIXED_UNIVERSE
from ibi.data_engine.sec_edgar.source_bytes import sha256_hex
from ibi.db.base import Base
from ibi.db.models import EntityRecord
from ibi.research.fundamentals import annual_fundamentals
from tests.integration.test_research_fundamentals import engine as engine
from tests.integration.test_sec_edgar_ingestion import AAPL, FakeConnector, _load

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/sec_edgar/company_tickers_exchange.json"
T0 = datetime(2026, 11, 15, 12, tzinfo=UTC)


@pytest.fixture(autouse=True)
def forbid_sec_and_ingestion(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("routes must never construct an SEC client, fetch or ingest")

    monkeypatch.setattr(SecHttpClient, "__init__", forbidden)
    monkeypatch.setattr("urllib.request.urlopen", forbidden)
    monkeypatch.setattr(ingest, "run_ingestion", forbidden)
    monkeypatch.setattr(listings, "fetch_listings", forbidden)


def client(engine):
    return TestClient(create_app(sessionmaker(bind=engine)))


def refresh(engine, directory, body=None):
    raw = FIXTURE.read_bytes() if body is None else json.dumps(body).encode()
    when = T0 if body is None else T0 + timedelta(minutes=1)
    with Session(engine) as session, session.begin():
        return listings.refresh_listings(
            session, directory, listings.FetchedListings(raw, sha256_hex(raw), when)
        )


@pytest.fixture
def catalog(engine, tmp_path):
    source = refresh(engine, tmp_path)
    with Session(engine) as session, session.begin():
        ingest.ingest_entity(
            session,
            FakeConnector(_load("aapl_submissions.json"), _load("aapl_companyfacts.json")),
            AAPL,
            tmp_path,
        )
    return SimpleNamespace(engine=engine, directory=tmp_path, source=source, client=client(engine))


def conflicts(catalog):
    body = json.loads(FIXTURE.read_bytes())
    for row in body["data"][:2]:
        body["data"].append([row[0], row[1], "AMB", row[3]])
    return refresh(catalog.engine, catalog.directory, body)


def assert_snapshot(body, source_id, when=T0):
    snapshot = body["listings_snapshot"]
    assert snapshot["source_document_id"] == source_id
    assert snapshot["retrieval_date"] == when.isoformat()
    assert snapshot["source_url"] == listings.LISTINGS_URL
    assert snapshot["resolution_source"] == "sec_listings"
    assert snapshot["fallback_reason"] is None


def test_search_ticker_name_exchange_status_and_provenance(catalog):
    response = catalog.client.get("/api/companies", params={"q": "micro"})
    (result,) = response.json()
    assert result["ticker"] == "MSFT" and result["exchange"] == "Nasdaq"
    assert result["name"] == "MICROSOFT CORP" and not result["ingested"]
    assert result["ingestion_status"] == "not_ingested"
    assert_snapshot(result, catalog.source.source_document_id)
    assert response.headers["X-Listings-Source-Document-ID"] == str(
        catalog.source.source_document_id
    )
    (apple,) = catalog.client.get("/api/companies", params={"q": "aapl"}).json()
    assert apple["ingested"] and apple["ingestion_status"] == "ingested"
    page = catalog.client.get("/", params={"q": "micro"})
    assert "MSFT" in page.text and "MICROSOFT CORP" in page.text
    assert "Nasdaq" in page.text and "Not yet ingested" in page.text
    assert "/companies/CIK0000789019" in page.text
    exact = catalog.client.get("/", params={"q": "nvda"})
    assert "http-equiv=refresh" in exact.text and "/companies/CIK0001045810" in exact.text
    assert "http-equiv=refresh" not in page.text


def test_ingested_statement_v2_financial_payload_is_unchanged(catalog):
    response = catalog.client.get(
        "/api/companies/AAPL/fundamentals", params={"as_of": T0.isoformat()}
    )
    assert response.status_code == 200
    body = response.json()
    with Session(catalog.engine) as session:
        expected = _jsonable(annual_fundamentals(session, AAPL.entity_id, AAPL.cik, T0), AAPL)
    for field in (
        "as_of",
        "statement_version",
        "non_reliance_coverage",
        "basis",
        "fiscal_years",
        "line_items",
        "derived",
    ):
        assert body[field] == expected[field]
    assert body["statement_version"] == "2" and body["ingestion_status"] == "ingested"
    assert_snapshot(body, catalog.source.source_document_id)
    assert any(
        cell["source"] and "www.sec.gov/Archives/" in cell["source"]["url"]
        for row in body["line_items"]
        for cell in row["values"].values()
    )
    page = catalog.client.get("/companies/aapl")
    assert page.status_code == 200 and "Income statement" in page.text
    assert "Sources and evidence" in page.text and "statement v2" in page.text
    assert f"source document {catalog.source.source_document_id}" in page.text
    assert T0.isoformat() in page.text


@pytest.mark.parametrize("query", ["NVDA", "nvda", "1045810", "CIK0001045810"])
def test_not_ingested_has_no_figures_and_has_exact_operator_command(catalog, query):
    api = catalog.client.get(f"/api/companies/{query}/fundamentals")
    assert api.status_code == 200
    body = api.json()
    assert body["company"]["name"] == "NVIDIA CORP"
    assert body["company"]["cik"] == 1045810 and body["company"]["exchange"] == "Nasdaq"
    assert body["ingestion_status"] == "not_ingested"
    assert not ({"line_items", "fiscal_years", "derived"} & body.keys())
    assert body["ingest_command"] == "python -m ibi.data_engine.sec_edgar.ingest --ticker NVDA"
    assert_snapshot(body, catalog.source.source_document_id)
    page = catalog.client.get(f"/companies/{query}")
    assert page.status_code == 200 and "Not yet ingested" in page.text
    assert "NVIDIA CORP" in page.text and "1045810" in page.text and "Nasdaq" in page.text
    assert body["ingest_command"] in page.text and T0.isoformat() in page.text
    assert "<table" not in page.text


@pytest.mark.parametrize("query", ["ZZZZ_UNKNOWN", "MICRO", "NVIDIA CORP"])
def test_unknown_and_name_paths_are_404_with_search_link(catalog, query):
    page = catalog.client.get(f"/companies/{query}")
    assert page.status_code == 404 and "Back to company search" in page.text
    assert "href='/'" in page.text and "<table" not in page.text
    api = catalog.client.get(f"/api/companies/{query}/fundamentals")
    assert api.status_code == 404 and api.json()["error_type"] == "Unknown"
    assert "line_items" not in api.json()
    assert_snapshot(api.json(), catalog.source.source_document_id)


def test_conflict_exposes_every_candidate_by_cik_with_no_figures(catalog):
    source = conflicts(catalog)
    api = catalog.client.get("/api/companies/AMB/fundamentals")
    assert api.status_code == 409
    body = api.json()
    assert body["error_type"] == "ConflictingEvidence" and "line_items" not in body
    assert {c["cik"] for c in body["candidates"]} == {320193, 789019}
    assert {c["ticker"] for c in body["candidates"]} == {"AMB"}
    assert {c["name"] for c in body["candidates"]} == {"Apple Inc.", "MICROSOFT CORP"}
    assert_snapshot(body, source.source_document_id, T0 + timedelta(minutes=1))
    for path in ("/companies/AMB", "/?q=AMB"):
        page = catalog.client.get(path)
        assert page.status_code == 409 and "Conflicting ticker" in page.text
        assert "/companies/CIK0000320193" in page.text and "/companies/CIK0000789019" in page.text
        assert "<table" not in page.text and "http-equiv=refresh" not in page.text
    assert catalog.client.get("/companies/CIK0000320193").status_code == 200
    body = catalog.client.get("/api/companies/CIK0000789019/fundamentals").json()
    assert body["ingest_command"].endswith("--ticker CIK0000789019")


def test_entity_row_defines_ingested_even_without_any_financial_facts(catalog):
    with Session(catalog.engine) as session, session.begin():
        session.add(
            EntityRecord(entity_id="CIK0001045810", kind="company", canonical_name="NVIDIA")
        )
    (result,) = catalog.client.get("/api/companies?q=NVDA").json()
    assert result["ingested"]
    body = catalog.client.get("/api/companies/NVDA/fundamentals").json()
    assert body["ingestion_status"] == "ingested" and body["fiscal_years"] == []
    assert "No annual (10-K) figures" in catalog.client.get("/companies/NVDA").text


def test_latest_nonempty_snapshot_never_falls_back_to_stale_symbols(catalog):
    body = json.loads(FIXTURE.read_bytes())
    body["data"] = [row for row in body["data"] if row[2] != "AAPL"]
    source = refresh(catalog.engine, catalog.directory, body)
    assert catalog.client.get("/companies/AAPL").status_code == 404
    api = catalog.client.get("/api/companies/CIK0000320193/fundamentals")
    assert api.status_code == 404
    assert_snapshot(api.json(), source.source_document_id, T0 + timedelta(minutes=1))


def test_empty_newest_snapshot_uses_defaults_instead_of_an_older_directory(catalog):
    source = refresh(catalog.engine, catalog.directory, {"fields": listings.FIELDS, "data": []})
    matches = catalog.client.get("/api/companies").json()
    assert {row["ticker"] for row in matches} == {"AAPL", "MSFT"}
    assert catalog.client.get("/companies/NVDA").status_code == 404
    body = catalog.client.get("/api/companies/AAPL/fundamentals").json()
    assert body["ingestion_status"] == "ingested"
    assert body["listings_snapshot"]["source_document_id"] == source.source_document_id
    assert body["listings_snapshot"]["fallback_reason"] == "empty_listings_snapshot"


@pytest.mark.integration
@pytest.mark.parametrize("engine", ["postgresql"], indirect=True)
def test_concurrent_refresh_cannot_mix_search_results_and_snapshot_provenance(catalog, monkeypatch):
    search = listings.search_listings
    published = []

    def after_directory_read(session, query, limit=20):
        if not published:
            body = json.loads(FIXTURE.read_bytes())
            body["data"] = [row for row in body["data"] if row[2] != "MSFT"]
            # Simulate a separate writer, after the request pinned its read
            # snapshot but before it searches. PostgreSQL MVCC permits commit.
            published.append(refresh(catalog.engine, catalog.directory, body))
        return search(session, query, limit)

    monkeypatch.setattr(listings, "search_listings", after_directory_read)
    first = catalog.client.get("/api/companies?q=micro").json()
    assert len(first) == 1 and first[0]["ticker"] == "MSFT"
    assert first[0]["listings_snapshot"]["source_document_id"] == catalog.source.source_document_id
    second = catalog.client.get("/api/companies?q=micro")
    assert second.json() == []
    assert second.headers["X-Listings-Source-Document-ID"] == str(published[0].source_document_id)


@pytest.mark.parametrize("empty", [False, True])
def test_fixed_universe_fallback_for_missing_or_empty_snapshot(engine, tmp_path, empty):
    source = refresh(engine, tmp_path, {"fields": listings.FIELDS, "data": []}) if empty else None
    http = client(engine)
    matches = http.get("/api/companies").json()
    assert {row["ticker"] for row in matches} == {c.ticker for c in FIXED_UNIVERSE}
    for row in matches:
        assert not row["ingested"]
        assert row["listings_snapshot"]["resolution_source"] == "fixed_universe"
    for path in ("/companies/AAPL", "/companies/CIK0000320193", "/companies/320193"):
        response = http.get(path)
        assert response.status_code == 200 and "Not yet ingested" in response.text
        assert "fixed default coverage" in response.text and "listings refresh" in response.text
    body = http.get("/api/companies/AAPL/fundamentals").json()
    assert body["listings_snapshot"]["source_document_id"] == (
        source.source_document_id if empty else None
    )
    assert body["listings_snapshot"]["retrieval_date"] == (
        (T0 + timedelta(minutes=1)).isoformat() if empty else None
    )
    assert body["listings_snapshot"]["fallback_reason"] == (
        "empty_listings_snapshot" if empty else "missing_listings_snapshot"
    )
    assert http.get("/companies/NVDA").status_code == 404
    assert "/companies/AAPL" in http.get("/?q=aapl").text


def test_search_is_limited_to_twenty_and_empty_results_retain_provenance(catalog):
    body = json.loads(FIXTURE.read_bytes())
    body["data"] += [[9000000 + i, "Common Search Company", f"Q{i}", None] for i in range(30)]
    source = refresh(catalog.engine, catalog.directory, body)
    matches = catalog.client.get("/api/companies?q=Common").json()
    assert len(matches) == 20
    assert all(
        row["listings_snapshot"]["source_document_id"] == source.source_document_id
        for row in matches
    )
    for query in ("%", "_", "' OR 1=1 --", "NOT_A_COMPANY"):
        response = catalog.client.get("/api/companies", params={"q": query})
        assert response.json() == []
        assert response.headers["X-Listings-Source-Document-ID"] == str(source.source_document_id)
        assert (
            response.headers["X-Listings-Retrieval-Date"] == (T0 + timedelta(minutes=1)).isoformat()
        )


class Markup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.commands = []
        self.in_code = False

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        self.in_code = tag == "code" or self.in_code

    def handle_endtag(self, tag):
        if tag == "code":
            self.in_code = False

    def handle_data(self, value):
        if self.in_code and value.startswith("python -m"):
            self.commands.append(value)


def test_untrusted_listing_names_tickers_and_commands_are_escaped(catalog):
    body = json.loads(FIXTURE.read_bytes())
    name = "<script>window.bad=true</script> & Partners"
    ticker = "X';echo BAD;&<svg>"
    body["data"].append([9999999, name, ticker, "A&B"])
    refresh(catalog.engine, catalog.directory, body)
    for path in ("/?q=Partners", "/companies/CIK0009999999"):
        response = catalog.client.get(path)
        markup = Markup()
        markup.feed(response.text)
        assert not ({"script", "svg", "img"} & {tag for tag, _ in markup.tags})
        assert "&lt;script&gt;" in response.text and "&amp; Partners" in response.text
        if markup.commands:
            assert shlex.split(markup.commands[0])[-1] == ticker.upper()
    api = catalog.client.get("/api/companies/CIK0009999999/fundamentals")
    assert api.json()["company"]["name"] == name
    assert api.headers["Content-Type"].startswith("application/json")


@pytest.mark.parametrize("fallback", [False, True])
def test_all_paths_and_states_never_write_commit_download_or_ingest(catalog, monkeypatch, fallback):
    if fallback:
        refresh(catalog.engine, catalog.directory, {"fields": listings.FIELDS, "data": []})
    else:
        conflicts(catalog)
    with catalog.engine.connect() as connection:
        before = {
            table.name: connection.execute(select(table)).all()
            for table in Base.metadata.sorted_tables
        }

    def no_commit(*args):
        pytest.fail("research route called commit")

    def no_writes(connection, cursor, statement, parameters, context, executemany):
        assert (
            not statement.lstrip()
            .upper()
            .startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "TRUNCATE"))
        )

    monkeypatch.setattr(Session, "commit", no_commit)
    event.listen(Session, "before_commit", no_commit)
    event.listen(catalog.engine, "before_cursor_execute", no_writes)
    try:
        paths = {
            "/healthz": 200,
            "/": 200,
            "/?q=micro": 200,
            "/?q=NVDA": 200,
            "/?q=AMB": 409,
            "/api/companies?q=AAPL": 200,
            "/api/companies?q=unknown": 200,
            "/companies/AAPL": 200,
            "/api/companies/AAPL/fundamentals": 200,
            "/companies/NVDA": 200,
            "/api/companies/NVDA/fundamentals": 200,
            "/companies/AMB": 409,
            "/api/companies/AMB/fundamentals": 409,
            "/companies/UNKNOWN": 404,
            "/api/companies/UNKNOWN/fundamentals": 404,
            "/companies/CIK0000789019": 200,
            "/api/companies/CIK0000320193/fundamentals": 200,
            "/api/companies/AAPL/fundamentals?as_of=2024-01-01T00:00:00": 422,
        }
        if fallback:
            paths.update(
                {
                    "/?q=AMB": 200,
                    "/companies/AMB": 404,
                    "/api/companies/AMB/fundamentals": 404,
                    "/companies/NVDA": 404,
                    "/api/companies/NVDA/fundamentals": 404,
                }
            )
        for path, status in paths.items():
            assert catalog.client.get(path).status_code == status, path
    finally:
        event.remove(catalog.engine, "before_cursor_execute", no_writes)
        event.remove(Session, "before_commit", no_commit)
    with catalog.engine.connect() as connection:
        for table in Base.metadata.sorted_tables:
            assert connection.execute(select(table)).all() == before[table.name]
