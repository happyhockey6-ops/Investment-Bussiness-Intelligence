"""SEC listing snapshots on migrated SQLite and isolated PostgreSQL schemas.

No test downloads anything. All refreshes use committed or synthetic bytes.
"""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from ibi.config import get_settings
from ibi.core.types import ConflictingEvidence, Unknown
from ibi.data_engine.sec_edgar import ingest, listings
from ibi.data_engine.sec_edgar.source_bytes import sha256_hex
from ibi.db.models import EntityRecord, SecurityListingRecord, SourceDocumentRecord

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/fixtures/sec_edgar/company_tickers_exchange.json"
PREVIOUS = "c4d2e8f1a7b3"
T0 = datetime(2026, 11, 13, tzinfo=UTC)


@pytest.fixture(params=["sqlite", pytest.param("postgresql", marks=pytest.mark.integration)])
def database(request, monkeypatch, tmp_path):
    admin = engine = None
    schema = "listings_test_" + uuid4().hex
    try:
        if request.param == "postgresql":
            url = make_url(request.getfixturevalue("postgres_test_url"))
            admin = create_engine(url, connect_args={"connect_timeout": 3})
            with admin.begin() as connection:
                connection.execute(CreateSchema(schema))
            monkeypatch.setenv("PGOPTIONS", f"-csearch_path={schema}")
        else:
            url = make_url(f"sqlite:///{tmp_path / 'listings.db'}")
        monkeypatch.setenv("IBI_DATABASE_URL", url.render_as_string(hide_password=False))
        get_settings.cache_clear()
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "alembic"))
        command.upgrade(config, "head")
        engine = create_engine(url)

        def forbidden(*args, **kwargs):
            pytest.fail("listing integration tests must not use the network")

        monkeypatch.setattr("urllib.request.urlopen", forbidden)
        yield engine, config, tmp_path / "raw"
    finally:
        if engine is not None:
            engine.dispose()
        if admin is not None:
            with admin.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
            admin.dispose()
        get_settings.cache_clear()


def payload(rows):
    return json.dumps({"fields": listings.FIELDS, "data": rows}).encode()


def refresh(database, raw=None, *, step=0):
    engine, _, directory = database
    raw = FIXTURE.read_bytes() if raw is None else raw
    snapshot = listings.FetchedListings(raw, sha256_hex(raw), T0 + timedelta(seconds=step))
    with Session(engine) as session, session.begin():
        return listings.refresh_listings(session, directory, snapshot)


def all_rows(engine, model):
    with engine.connect() as connection:
        return connection.execute(
            select(model.__table__).order_by(*model.__table__.primary_key.columns)
        ).all()


def test_fixture_archival_digest_and_no_entity_ingestion(database):
    engine, _, directory = database
    outcome = refresh(database)
    assert outcome.status == "created" and outcome.listings_written == 20
    (source,) = all_rows(engine, SourceDocumentRecord)
    assert source.id == outcome.source_document_id
    assert source.source_url == listings.LISTINGS_URL
    assert source.source_tier == "primary_regulatory"
    assert source.content_sha256 == sha256_hex(FIXTURE.read_bytes())
    assert source.content_length == len(FIXTURE.read_bytes())
    archived = Path(source.content_ref)
    assert archived.parent == directory / "sec_edgar/_reference"
    assert archived.read_bytes() == FIXTURE.read_bytes()
    assert archived.stat().st_mode & 0o222 == 0
    assert len(all_rows(engine, SecurityListingRecord)) == 20
    assert all_rows(engine, EntityRecord) == []


def test_unchanged_then_changed_snapshot_preserves_all_history(database):
    engine, _, directory = database
    first = refresh(database)
    old_rows = all_rows(engine, SecurityListingRecord)
    old_documents = all_rows(engine, SourceDocumentRecord)
    again = refresh(database)  # even an identical timestamp cannot collide on disk
    assert again.status == "unchanged" and again.listings_written == 0
    assert again.source_document_id == first.source_document_id
    assert all_rows(engine, SecurityListingRecord) == old_rows
    assert all_rows(engine, SourceDocumentRecord) == old_documents
    assert len(list(directory.rglob("*.json"))) == 1
    raw = payload([[320193, "Apple Inc.", "NEW-AAPL", "Nasdaq"]])
    second = refresh(database, raw, step=1)
    assert second.status == "created" and second.source_document_id != first.source_document_id
    assert all_rows(engine, SecurityListingRecord)[:20] == old_rows
    assert all_rows(engine, SourceDocumentRecord)[:1] == old_documents
    with Session(engine) as session:
        assert isinstance(listings.resolve_ticker(session, "AAPL"), Unknown)
        assert listings.resolve_ticker(session, "new-aapl").cik == 320193
        assert listings.search_listings(session, "MICROSOFT") == []
    # A -> B -> A is a new snapshot, not a global hash dedup that hides B's history.
    third = refresh(database, step=2)
    assert third.status == "created" and len(all_rows(engine, SourceDocumentRecord)) == 3


def test_empty_latest_snapshot_does_not_fall_back_to_delisted_rows(database):
    engine, _, _ = database
    refresh(database)
    outcome = refresh(database, payload([]), step=1)
    assert outcome.status == "created" and outcome.listings_written == 0
    with Session(engine) as session:
        assert isinstance(listings.resolve_ticker(session, "AAPL"), Unknown)
        assert listings.search_listings(session, "Apple") == []
    assert len(all_rows(engine, SecurityListingRecord)) == 20


def test_delayed_older_fetch_cannot_replace_newer_snapshot_and_time_ties_are_stable(database):
    engine, _, _ = database
    refresh(database, payload([[1, "New company", "NEW", None]]), step=10)
    refresh(database, payload([[2, "Old company", "OLD", None]]), step=0)
    with Session(engine) as session:
        assert listings.resolve_ticker(session, "NEW").cik == 1
        assert isinstance(listings.resolve_ticker(session, "OLD"), Unknown)
    # Different bytes at the same retrieval time have distinct archive names;
    # source-document insertion order supplies the final snapshot tie-break.
    refresh(database, payload([[3, "Tied company", "TIE", None]]), step=10)
    with Session(engine) as session:
        assert listings.resolve_ticker(session, "TIE").cik == 3
        assert isinstance(listings.resolve_ticker(session, "NEW"), Unknown)
    assert len(all_rows(engine, SecurityListingRecord)) == 3


@pytest.mark.parametrize(
    "raw",
    [
        b'{"fields":["cik","ticker","name","exchange"],"data":[]}',
        payload([["320193", "Apple Inc.", "AAPL", "Nasdaq"]]),
        payload([[320193, "Apple Inc.", " ", "Nasdaq"]]),
        b'{"fields":["cik","name","ticker","exchange"],"data":[[NaN,"A","A",null]]}',
        b"\xff",
    ],
)
def test_malformed_bytes_remain_archived_without_publishing_a_snapshot(database, raw):
    engine, _, directory = database
    refresh(database)
    before = all_rows(engine, SecurityListingRecord)
    with pytest.raises(listings.MalformedListingsError) as caught:
        refresh(database, raw, step=1)
    assert Path(caught.value.snapshot_ref).read_bytes() == raw
    assert len(list(directory.rglob("*.json"))) == 2
    assert all_rows(engine, SecurityListingRecord) == before
    assert len(all_rows(engine, SourceDocumentRecord)) == 1
    with Session(engine) as session:
        assert listings.resolve_ticker(session, "AAPL").cik == 320193


def test_resolution_and_search_are_case_insensitive_literal_and_read_only(database):
    engine, _, _ = database
    with Session(engine) as session:
        assert isinstance(listings.resolve_ticker(session, "AAPL"), Unknown)
    refresh(database)
    with Session(engine) as session:
        for query in ("AAPL", "aapl", " Aapl ", "Apple Inc.", "apple"):
            result = listings.resolve_ticker(session, query)
            assert result.cik == 320193 and result.source_document_id
        assert listings.resolve_ticker(session, "brk.b").cik == 1067983
        assert listings.resolve_ticker(session, "BRK-B").cik == 1067983
        assert listings.search_listings(session, "berkshire")[0].ticker == "BRK-B"
        assert listings.search_listings(session, "brk.b")[0].ticker == "BRK-B"
        assert listings.search_listings(session, "%") == []
        assert listings.search_listings(session, "_") == []
        assert listings.search_listings(session, "") == []
        assert isinstance(listings.resolve_ticker(session, "ZZZZ_UNKNOWN"), Unknown)
        assert isinstance(listings.resolve_ticker(session, ""), Unknown)
        assert len(listings.search_listings(session, "a", limit=2)) == 2
        # Reads must not autoflush an unrelated pending write.
        pending = SourceDocumentRecord(source="pending")
        session.add(pending)
        listings.resolve_ticker(session, "aapl")
        listings.search_listings(session, "apple")
        assert pending.id is None


def test_punctuation_aliases_with_two_ciks_are_conflicting(database):
    engine, _, _ = database
    refresh(database, payload([[1, "One", "AB.C", None], [2, "Two", "AB-C", "NYSE"]]))
    with Session(engine) as session:
        for query in ("ab.c", "AB-C"):
            answer = listings.resolve_ticker(session, query)
            assert isinstance(answer, ConflictingEvidence)
            assert {c.cik for c in answer.candidate_values} == {1, 2}
            assert all(c.source_document_id for c in answer.candidate_values)


@pytest.mark.parametrize(
    "rows",
    [
        [[1, "One", "DUP", None], [2, "Two", "DUP", "NYSE"]],
        [[2, "Two", "dup", "NYSE"], [1, "One", "DUP", None]],
    ],
)
def test_exact_duplicate_ticker_with_distinct_ciks_preserves_conflicting_evidence(database, rows):
    engine, _, _ = database
    outcome = refresh(database, payload(rows))
    assert outcome.listings_written == 2
    with Session(engine) as session:
        answer = listings.resolve_ticker(session, "dup")
        assert isinstance(answer, ConflictingEvidence)
        assert {candidate.cik for candidate in answer.candidate_values} == {1, 2}
        assert {c.source_document_id for c in answer.candidate_values} == {
            outcome.source_document_id
        }
        assert len(listings.search_listings(session, "DUP")) == 2
    assert {row.cik for row in all_rows(engine, SecurityListingRecord)} == {1, 2}


def test_unique_identity_is_enforced_and_full_cik_range_is_stored(database):
    engine, _, _ = database
    outcome = refresh(database, payload([[9_999_999_999, "Company", "BIG", None]]))
    with Session(engine) as session:
        assert listings.resolve_ticker(session, "BIG").entity_id == "CIK9999999999"
    with pytest.raises(IntegrityError):
        with Session(engine) as session, session.begin():
            session.add(
                SecurityListingRecord(
                    source_document_id=outcome.source_document_id,
                    cik=9_999_999_999,
                    entity_id="CIK9999999999",
                    ticker="BIG",
                    raw_ticker="BIG",
                    name="Duplicate",
                )
            )
    constraints = inspect(engine).get_unique_constraints("security_listings")
    assert any(c["column_names"] == ["source_document_id", "ticker", "cik"] for c in constraints)
    foreign_keys = inspect(engine).get_foreign_keys("security_listings")
    assert len(foreign_keys) == 1 and foreign_keys[0]["referred_table"] == "source_documents"
    assert any(
        i["column_names"] == ["ticker"] for i in inspect(engine).get_indexes("security_listings")
    )


def test_repeated_same_ticker_cik_is_archived_and_rejected_atomically(database):
    engine, _, _ = database
    raw = payload([[1, "One", "DUP", None], [1, "Other", "dup", "NYSE"]])
    with pytest.raises(listings.MalformedListingsError, match="repeated ticker/CIK") as caught:
        refresh(database, raw)
    assert Path(caught.value.snapshot_ref).read_bytes() == raw
    assert not all_rows(engine, SecurityListingRecord)
    assert not all_rows(engine, SourceDocumentRecord)


def test_name_ambiguity_is_not_arbitrarily_resolved(database):
    engine, _, _ = database
    refresh(
        database,
        payload([[1, "Similar Company One", "A", None], [2, "Similar Company Two", "B", None]]),
    )
    with Session(engine) as session:
        assert isinstance(listings.resolve_ticker(session, "similar company"), ConflictingEvidence)
        assert listings.resolve_ticker(session, "similar company one").cik == 1


@pytest.mark.integration
@pytest.mark.parametrize("database", ["postgresql"], indirect=True)
def test_concurrent_postgresql_refreshes_publish_only_one_identical_snapshot(database):
    engine, _, directory = database
    raw = FIXTURE.read_bytes()
    snapshot = listings.FetchedListings(raw, sha256_hex(raw), T0)
    barrier = Barrier(2)

    def worker():
        with Session(engine) as session, session.begin():
            barrier.wait(timeout=10)
            return listings.refresh_listings(session, directory, snapshot)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(worker) for _ in range(2)]
        results = [future.result(timeout=20) for future in futures]
    assert sorted(r.status for r in results) == ["created", "unchanged"]
    assert len({r.source_document_id for r in results}) == 1
    assert len(all_rows(engine, SourceDocumentRecord)) == 1
    assert len(all_rows(engine, SecurityListingRecord)) == 20


def test_db_failure_rolls_back_every_new_listing_and_its_document(database):
    engine, _, directory = database
    refresh(database, payload([[320193, "Apple Inc.", "AAPL", "Nasdaq"]]))
    before = all_rows(engine, SecurityListingRecord)
    with engine.begin() as connection:
        if engine.dialect.name == "postgresql":
            connection.execute(
                text(
                    "ALTER TABLE security_listings ADD CONSTRAINT reject_msft "
                    "CHECK (ticker <> 'MSFT')"
                )
            )
        else:
            connection.execute(
                text(
                    "CREATE TRIGGER reject_msft BEFORE INSERT ON security_listings "
                    "WHEN NEW.ticker = 'MSFT' BEGIN "
                    "SELECT RAISE(ABORT, 'reject MSFT'); END"
                )
            )
    with pytest.raises(IntegrityError):
        refresh(database, step=1)
    assert all_rows(engine, SecurityListingRecord) == before
    assert len(all_rows(engine, SourceDocumentRecord)) == 1
    assert len(list(directory.rglob("*.json"))) == 2


def test_archive_failure_or_bad_digest_publishes_nothing(database, monkeypatch):
    engine, _, directory = database
    raw = FIXTURE.read_bytes()
    snapshot = listings.FetchedListings(raw, "0" * 64, T0)
    with pytest.raises(listings.MalformedListingsError, match="digest"):
        with Session(engine) as session, session.begin():
            listings.refresh_listings(session, directory, snapshot)

    def fail(*args):
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(ingest, "_write_new_file", fail)
    with pytest.raises(ingest.RawSnapshotWriteError, match="disk failure"):
        refresh(database)
    assert all_rows(engine, SourceDocumentRecord) == []
    assert all_rows(engine, SecurityListingRecord) == []


def test_identical_refresh_rechecks_archive_integrity(database):
    engine, _, _ = database
    first = refresh(database)
    path = Path(first.snapshot_ref)
    path.chmod(0o600)
    path.write_bytes(b"tampered")
    with pytest.raises(ingest.RawSnapshotWriteError, match="verification"):
        refresh(database)
    assert len(all_rows(engine, SourceDocumentRecord)) == 1


def test_migration_round_trip_and_downgrade_drops_only_new_table(database):
    engine, config, _ = database
    command.check(config)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    command.check(config)
    refresh(database)
    before_docs = all_rows(engine, SourceDocumentRecord)
    old_tables = set(inspect(engine).get_table_names()) - {"security_listings"}
    command.downgrade(config, PREVIOUS)
    assert set(inspect(engine).get_table_names()) == old_tables
    assert all_rows(engine, SourceDocumentRecord) == before_docs
    command.upgrade(config, "head")
    command.check(config)
    assert all_rows(engine, SourceDocumentRecord) == before_docs
    assert all_rows(engine, SecurityListingRecord) == []
