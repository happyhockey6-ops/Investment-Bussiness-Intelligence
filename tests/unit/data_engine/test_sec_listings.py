"""Pure parsing and fetch boundaries; every HTTP request is mocked."""

import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from ibi.config import Settings
from ibi.core.errors import ProviderError
from ibi.core.types import Unknown
from ibi.data_engine.sec_edgar import listings
from ibi.data_engine.sec_edgar.source_bytes import sha256_hex

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures/sec_edgar/company_tickers_exchange.json"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("listing tests must never make HTTP requests")

    monkeypatch.setattr("urllib.request.urlopen", forbidden)


def test_twenty_real_rows_and_pure_parsing():
    raw = FIXTURE.read_bytes()
    rows = listings.parse_listings(raw)
    assert len(rows) == 20
    assert (rows[0].cik, rows[0].ticker, rows[0].name) == (320193, "AAPL", "Apple Inc.")
    assert rows[0].entity_id == "CIK0000320193"
    assert any(r.ticker == "BRK-B" for r in rows)
    assert any(r.exchange is None for r in rows)
    assert rows == listings.parse_listings(raw)
    assert sha256_hex(raw) == "f9cd3364257b5f9fed1cd702236b2f0a8f580fe9493650da5c7cd16346696faa"


def test_raw_spelling_is_retained_while_ticker_is_uppercased():
    raw = json.dumps({"fields": listings.FIELDS, "data": [[1, "Company", " ab.c ", None]]}).encode()
    (row,) = listings.parse_listings(raw)
    assert row.raw_ticker == " ab.c " and row.ticker == "AB.C"


def test_parser_keeps_distinct_ciks_for_the_exact_same_ticker():
    raw = json.dumps(
        {"fields": listings.FIELDS, "data": [[1, "One", "DUP", None], [2, "Two", "dup", "NYSE"]]}
    ).encode()
    rows = listings.parse_listings(raw)
    assert [row.cik for row in rows] == [1, 2]
    assert [row.ticker for row in rows] == ["DUP", "DUP"]
    assert [row.raw_ticker for row in rows] == ["DUP", "dup"]


def test_parser_rejects_repeated_ticker_cik_instead_of_discarding_metadata():
    raw = json.dumps(
        {"fields": listings.FIELDS, "data": [[1, "One", "DUP", None], [1, "Other", "dup", "NYSE"]]}
    ).encode()
    with pytest.raises(listings.MalformedListingsError, match="repeated ticker/CIK"):
        listings.parse_listings(raw)


@pytest.mark.parametrize(
    "raw",
    [
        b"{}",
        b'{"fields": ["name","cik","ticker","exchange"], "data": []}',
        b'{"fields": ["cik","name","ticker","exchange"], "data": {}}',
        b'{"fields": ["cik","name","ticker","exchange"]}',
        b"[]",
        b"{",
        b"\xff",
        b'{"val": NaN}',
        b'{"val": Infinity}',
    ],
)
def test_bad_document_is_typed(raw):
    with pytest.raises(listings.MalformedListingsError):
        listings.parse_listings(raw)


@pytest.mark.parametrize(
    "row",
    [
        ["1", "Company", "ABC", None],
        [1.0, "Company", "ABC", None],
        [True, "Company", "ABC", None],
        [0, "Company", "ABC", None],
        [-1, "Company", "ABC", None],
        [10_000_000_000, "Company", "ABC", None],
        [1, "Company", "", None],
        [1, "Company", "  ", None],
        [1, "Company", None, None],
        [1, None, "ABC", None],
        [1, "Company", "ABC", 5],
        [1, "Company", "ABC"],
        None,
    ],
)
def test_bad_row_is_typed(row):
    raw = json.dumps({"fields": listings.FIELDS, "data": [row]}).encode()
    with pytest.raises(listings.MalformedListingsError, match="row 0"):
        listings.parse_listings(raw)


def test_fetch_preserves_even_malformed_bytes_without_a_parsing_connector():
    raw = b"\xff not JSON\r\n"
    calls = []
    client = SimpleNamespace(get_bytes=lambda url: calls.append(url) or raw)
    settings = Settings(sec_user_agent="Listing tests tests@example.com", _env_file=None)
    fetched = listings.fetch_listings(settings, client=client)
    assert fetched.raw_bytes == raw and fetched.sha256 == sha256_hex(raw)
    assert fetched.retrieved_at.tzinfo is not None
    assert calls == [listings.LISTINGS_URL]


@pytest.mark.parametrize("contact", [None, "", "Tests <CONTACT_EMAIL>", "Tests", "a@example.com"])
def test_fetch_requires_real_contact_before_http(contact):
    settings = Settings(sec_user_agent=contact, _env_file=None)
    with pytest.raises(RuntimeError, match="IBI_SEC_USER_AGENT"):
        listings.fetch_listings(settings)


def test_missing_reference_document_is_not_an_empty_success():
    settings = Settings(sec_user_agent="Listing tests tests@example.com", _env_file=None)
    client = SimpleNamespace(get_bytes=lambda url: Unknown("404"))
    with pytest.raises(ProviderError, match="404"):
        listings.fetch_listings(settings, client=client)


@pytest.mark.parametrize("contact", [None, "Tests <CONTACT_EMAIL>", "Invalid"])
def test_cli_refuses_missing_contact_without_http_or_database(monkeypatch, capsys, contact):
    settings = Settings(sec_user_agent=contact, _env_file=None)
    monkeypatch.setattr(listings, "get_settings", lambda: settings)

    def forbidden(*args):
        pytest.fail("invalid contact must fail before opening the database")

    monkeypatch.setattr(listings, "session_scope", forbidden)
    assert listings.main(["refresh"]) == 1
    assert "IBI_SEC_USER_AGENT" in capsys.readouterr().err


@pytest.mark.parametrize("status", ["created", "unchanged"])
def test_cli_refresh_passes_exact_fetched_bytes_and_reports_status(monkeypatch, capsys, status):
    raw = FIXTURE.read_bytes()
    settings = Settings(sec_user_agent="Tests tests@example.com", _env_file=None)
    monkeypatch.setattr(listings, "get_settings", lambda: settings)
    calls = []

    def get_bytes(self, url):
        calls.append((url, self._config.user_agent))
        return raw

    monkeypatch.setattr(listings.SecHttpClient, "get_bytes", get_bytes)
    session = object()
    monkeypatch.setattr(listings, "session_scope", lambda setting: nullcontext(session))

    def refresh(current_session, directory, snapshot):
        assert current_session is session and directory == Path(settings.raw_data_dir)
        assert snapshot.raw_bytes == raw and snapshot.sha256 == sha256_hex(raw)
        return listings.RefreshResult(status, 1, 20 if status == "created" else 0, "/test/archive")

    monkeypatch.setattr(listings, "refresh_listings", refresh)
    assert listings.main(["refresh"]) == 0
    assert calls == [(listings.LISTINGS_URL, settings.sec_user_agent)]
    assert capsys.readouterr().out.startswith(status + ": source_document_id=1")


def test_cli_does_not_report_success_on_provider_failure(monkeypatch, capsys):
    settings = Settings(sec_user_agent="Tests tests@example.com", _env_file=None)
    monkeypatch.setattr(listings, "get_settings", lambda: settings)
    monkeypatch.setattr(listings.SecHttpClient, "get_bytes", lambda *args: Unknown("404"))
    assert listings.main(["refresh"]) == 1
    output = capsys.readouterr()
    assert not output.out and "ProviderError" in output.err
