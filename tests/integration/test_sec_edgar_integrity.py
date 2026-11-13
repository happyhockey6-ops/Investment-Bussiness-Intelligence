"""Company transactions, conflicts and explicit outcomes on SQLite/PostgreSQL.

All network data is synthetic. PostgreSQL uses a new migrated schema inside
IBI_TEST_POSTGRES_URL; no application/production database is accessed.
"""

import asyncio
import copy
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from ibi.config import Settings, get_settings
from ibi.core.epistemics import Provenance, SourceTier
from ibi.data_engine.interfaces import RawRecord
from ibi.data_engine.sec_edgar import ingest
from ibi.data_engine.sec_edgar.fixed_universe import FixedUniverseEntity
from ibi.data_engine.sec_edgar.source_bytes import sha256_hex
from ibi.db import base as db
from ibi.db.models import EntityRecord, FilingRecord, FinancialDataPointRecord, SourceDocumentRecord

COMPANIES = tuple(
    FixedUniverseEntity(cik=n, entity_id=f"CIK{n:010d}", canonical_name=f"Test company {n}")
    for n in (1, 2, 3)
)
MIDDLE = COMPANIES[1].entity_id


def documents(company):
    accession = f"{company.cik:010d}-24-000001"
    entry = {"accn": accession, "end": "2023-12-31", "filed": "2024-02-01", "form": "10-K"}
    return {
        "submissions": {"filings": {"recent": {
            "accessionNumber": [accession], "filingDate": ["2024-02-01"], "form": ["10-K"],
            "reportDate": ["2023-12-31"], "primaryDocument": ["test.htm"], "items": [""],
            "acceptanceDateTime": ["2024-02-01T15:00:00Z"],
        }}},
        "companyfacts": {"facts": {"us-gaap": {
            "Assets": {"units": {"USD": [dict(entry, val=10)]}},
            "Revenues": {"units": {"USD": [dict(entry, val=20, start="2023-01-01")]}},
        }}},
    }


class BatchConnector:
    def __init__(self):
        self.payloads = {c.entity_id: documents(c) for c in COMPANIES}
        self.failures = {}
        self.calls = []

    def fetch(self, entity_id):
        self.calls.append(entity_id)
        if entity_id in self.failures:
            raise self.failures[entity_id]
        now = datetime.now(UTC)
        return [
            RawRecord(
                entity_id=entity_id,
                payload={"kind": kind, "raw_bytes": (raw := json.dumps(body).encode()),
                         "sha256": sha256_hex(raw)},
                provenance=Provenance(
                    source="SEC EDGAR", source_tier=SourceTier.PRIMARY_REGULATORY,
                    retrieval_date=now, source_url=f"https://data.sec.gov/{entity_id}/{kind}.json",
                ),
            )
            for kind, body in self.payloads[entity_id].items()
        ]


@pytest.fixture(params=["sqlite", pytest.param("postgresql", marks=pytest.mark.integration)])
def batch(request, monkeypatch, tmp_path):
    admin = engine = None
    schema = "ingest_test_" + uuid4().hex
    try:
        if request.param == "postgresql":
            url = make_url(request.getfixturevalue("postgres_test_url"))
            admin = create_engine(url, connect_args={"connect_timeout": 3})
            with admin.begin() as connection:
                connection.execute(CreateSchema(schema))
            monkeypatch.setenv("PGOPTIONS", f"-csearch_path={schema}")
        else:
            url = make_url(f"sqlite:///{tmp_path / 'ingest.db'}")
        monkeypatch.setenv("IBI_DATABASE_URL", url.render_as_string(hide_password=False))
        get_settings.cache_clear()
        root = Path(__file__).resolve().parents[2]
        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "alembic"))
        command.upgrade(config, "head")
        engine = create_engine(url)
        # Exercise the real session_scope commit/rollback with one fresh Session per company.
        monkeypatch.setattr(
            db, "_SessionFactory", sessionmaker(bind=engine, expire_on_commit=False)
        )
        connector = BatchConnector()
        monkeypatch.setattr(ingest, "FIXED_UNIVERSE", COMPANIES)
        monkeypatch.setattr(ingest, "SecEdgarConnector", lambda client: connector)
        settings = Settings(
            database_url=url.render_as_string(hide_password=False),
            raw_data_dir=str(tmp_path / "raw"), sec_user_agent="Tests test@example.com",
            _env_file=None,
        )
        yield SimpleNamespace(engine=engine, connector=connector, settings=settings)
    finally:
        if engine is not None:
            engine.dispose()
        if admin is not None:
            with admin.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
            admin.dispose()
        get_settings.cache_clear()


def state(batch, entity_id):
    """Fresh connections observe committed rows, including all provenance writes."""
    with batch.engine.connect() as connection:
        return {
            "entities": connection.execute(select(EntityRecord.__table__).where(
                EntityRecord.entity_id == entity_id
            )).all(),
            "filings": connection.execute(select(FilingRecord.__table__).where(
                FilingRecord.entity_id == entity_id
            ).order_by(FilingRecord.id)).all(),
            "facts": connection.execute(select(FinancialDataPointRecord.__table__).where(
                FinancialDataPointRecord.entity_id == entity_id
            ).order_by(FinancialDataPointRecord.id)).all(),
            "sources": connection.execute(select(SourceDocumentRecord.__table__).where(
                SourceDocumentRecord.content_ref.contains(entity_id)
            ).order_by(SourceDocumentRecord.id)).all(),
        }


def assert_isolated(batch, summary):
    assert [r.entity_id for r in summary.results] == [c.entity_id for c in COMPANIES]
    assert [r.status for r in summary.results] == ["success", "failed", "success"]
    assert summary.had_any_error
    assert batch.connector.calls == [c.entity_id for c in COMPANIES]
    for index in (0, 2):
        result = summary.results[index]
        assert result.error is None and result.error_type is None
        assert result.facts_written == 2
        assert {key: len(rows) for key, rows in state(batch, result.entity_id).items()} == {
            "entities": 1, "filings": 1, "facts": 2, "sources": 2,
        }
    failed = summary.results[1]
    assert failed.error_type and failed.error
    assert failed.facts_written == failed.filings_written == failed.source_documents_written == 0
    assert all(not rows for rows in state(batch, MIDDLE).values())
    return failed


def test_database_error_rolls_back_middle_company_and_continues(batch):
    # Test-only fault: the middle company's second fact violates a real DB index.
    # Entity, source documents, filing and its first fact have already been flushed.
    with batch.engine.begin() as connection:
        connection.exec_driver_sql(
            f"CREATE UNIQUE INDEX fail_middle ON financial_data (entity_id) "
            f"WHERE entity_id = '{MIDDLE}'"
        )
    failed = assert_isolated(batch, ingest.run_ingestion(batch.settings))
    assert failed.error_type == "IntegrityError"
    assert len(failed.raw_snapshot_refs) == 2
    assert all(Path(ref).is_file() for ref in failed.raw_snapshot_refs)


def test_commit_error_is_a_failed_outcome_not_a_success(batch):
    def fail_middle_commit(session):
        if session.scalar(select(EntityRecord).where(EntityRecord.entity_id == MIDDLE)):
            # Actual database error at commit time, after ingest_entity returned.
            session.execute(text("SELECT * FROM ingestion_commit_probe_missing"))

    event.listen(Session, "before_commit", fail_middle_commit)
    try:
        summary = ingest.run_ingestion(batch.settings)
    finally:
        event.remove(Session, "before_commit", fail_middle_commit)
    failed = assert_isolated(batch, summary)
    assert failed.error_type in ("ProgrammingError", "OperationalError")


@pytest.mark.parametrize("batch", [pytest.param("postgresql", marks=pytest.mark.integration)],
                         indirect=True)
def test_postgres_deferred_constraint_failure_at_commit(batch, monkeypatch):
    # Fault injection only in this disposable schema: all middle-company inserts
    # succeed, but PostgreSQL's COMMIT rejects a deferred foreign-key constraint.
    with batch.engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE ingestion_allowed (entity_id VARCHAR(64) PRIMARY KEY)"
        )
        connection.execute(
            text("INSERT INTO ingestion_allowed VALUES (:first), (:third)"),
            {"first": COMPANIES[0].entity_id, "third": COMPANIES[2].entity_id},
        )
        connection.exec_driver_sql(
            "ALTER TABLE financial_data ADD CONSTRAINT middle_commit_guard "
            "FOREIGN KEY (entity_id) REFERENCES ingestion_allowed(entity_id) "
            "DEFERRABLE INITIALLY DEFERRED"
        )
    staged = []
    original = ingest.ingest_entity

    def track_staged(*args, **kwargs):
        result = original(*args, **kwargs)
        staged.append(result.entity_id)
        return result

    monkeypatch.setattr(ingest, "ingest_entity", track_staged)
    failed = assert_isolated(batch, ingest.run_ingestion(batch.settings))
    assert staged == [c.entity_id for c in COMPANIES]
    assert failed.error_type == "IntegrityError"
    assert "middle_commit_guard" in failed.error


@pytest.mark.parametrize("malformed", ["missing_facts", "bad_value", "bad_arrays", "json", "nan"])
def test_malformed_payload_is_a_company_failure(batch, malformed):
    payload = batch.connector.payloads[MIDDLE]
    if malformed == "missing_facts":
        payload["companyfacts"] = {}
    elif malformed == "bad_value":
        payload["companyfacts"]["facts"]["us-gaap"]["Assets"]["units"]["USD"][0]["val"] = "bad"
    elif malformed == "bad_arrays":
        payload["submissions"]["filings"]["recent"]["form"] = []
    elif malformed == "json":
        batch.connector.failures[MIDDLE] = json.JSONDecodeError("bad SEC JSON", "{", 0)
    else:
        payload["companyfacts"]["facts"]["us-gaap"]["Assets"]["units"]["USD"][0]["val"] = "NaN"
    failed = assert_isolated(batch, ingest.run_ingestion(batch.settings))
    assert failed.error_type == "MalformedSecPayloadError"
    assert MIDDLE in failed.error
    if malformed != "json":
        assert len(failed.raw_snapshot_refs) == 2


def test_raw_snapshot_write_failure_rolls_back_and_continues(batch, monkeypatch):
    write = ingest._write_new_file

    def fail_middle_snapshot(path, data):
        if MIDDLE in path.parts and path.name.startswith("companyfacts_"):
            raise PermissionError("simulated read-only archive")
        return write(path, data)

    monkeypatch.setattr(ingest, "_write_new_file", fail_middle_snapshot)
    failed = assert_isolated(batch, ingest.run_ingestion(batch.settings))
    assert failed.error_type == "RawSnapshotWriteError"
    assert "simulated read-only archive" in failed.error
    assert len(failed.raw_snapshot_refs) == 1
    assert Path(failed.raw_snapshot_refs[0]).is_file()


def test_identical_duplicate_values_are_idempotent(batch):
    entries = batch.connector.payloads[MIDDLE]["companyfacts"]["facts"]["us-gaap"]["Assets"]
    entries["units"]["USD"].append(dict(entries["units"]["USD"][0], val=10.0))
    first = ingest.run_ingestion(batch.settings)
    assert not first.had_any_error
    assert first.results[1].facts_written == 2
    assert first.results[1].facts_skipped_duplicate == 1
    before = state(batch, MIDDLE)
    second = ingest.run_ingestion(batch.settings)
    assert not second.had_any_error
    assert all(r.status == "success" and r.error_type is None for r in second.results)
    assert all(r.facts_written == r.filings_written == 0 for r in second.results)
    assert second.results[1].facts_skipped_duplicate == 3
    after = state(batch, MIDDLE)
    assert after["facts"] == before["facts"] and after["filings"] == before["filings"]


def make_conflict(batch):
    tags = batch.connector.payloads[MIDDLE]["companyfacts"]["facts"]["us-gaap"]
    # A new fact is flushed before a later conflicting duplicate is discovered.
    tags["Assets"]["units"]["USD"][0]["val"] = 99
    new = copy.deepcopy(tags["Assets"])
    new["units"]["USD"][0]["val"] = 30
    batch.connector.payloads[MIDDLE]["companyfacts"]["facts"]["us-gaap"] = {
        "Liabilities": new, **tags,
    }


def test_conflict_preserves_original_and_evidence_and_continues(batch):
    assert not ingest.run_ingestion(batch.settings).had_any_error
    before = state(batch, MIDDLE)
    original = next(r for r in before["facts"] if r.metric_id == "us-gaap:Assets")
    make_conflict(batch)
    batch.connector.calls.clear()
    summary = ingest.run_ingestion(batch.settings)
    assert [r.status for r in summary.results] == ["success", "failed", "success"]
    assert batch.connector.calls == [c.entity_id for c in COMPANIES]
    failed = summary.results[1]
    assert failed.error_type == "DuplicateFactConflict"
    assert failed.facts_written == failed.filings_written == failed.source_documents_written == 0
    assert state(batch, MIDDLE) == before
    conflict = failed.conflict
    assert conflict.existing_fact_id == original.id
    assert conflict.existing_source_document_id == original.source_document_id
    assert (conflict.existing_value, conflict.incoming_value) == (Decimal(10), Decimal(99))
    assert conflict.accession_number == original.accession_number
    assert conflict.incoming_snapshot_ref in failed.raw_snapshot_refs
    archived = json.loads(Path(conflict.incoming_snapshot_ref).read_text())
    assert archived["facts"]["us-gaap"]["Assets"]["units"]["USD"][0]["val"] == 99


def test_single_company_caller_receives_typed_conflict(batch):
    assert not ingest.run_ingestion(batch.settings).had_any_error
    before = state(batch, MIDDLE)
    make_conflict(batch)
    with pytest.raises(ingest.DuplicateFactConflict) as error:
        with Session(batch.engine) as session, session.begin():
            ingest.ingest_entity(
                session, batch.connector, COMPANIES[1], Path(batch.settings.raw_data_dir)
            )
    assert error.value.conflict.incoming_value == Decimal(99)
    assert state(batch, MIDDLE) == before


@pytest.mark.parametrize("tag", ["Assets", "Revenues"])
def test_conflicting_entries_in_one_payload_roll_back_the_entire_company(batch, tag):
    entries = batch.connector.payloads[MIDDLE]["companyfacts"]["facts"]["us-gaap"][tag]["units"]
    entries["USD"].append(dict(entries["USD"][0], val=99))
    failed = assert_isolated(batch, ingest.run_ingestion(batch.settings))
    assert failed.error_type == "DuplicateFactConflict"
    assert failed.conflict.metric_id == f"us-gaap:{tag}"
    archived = json.loads(Path(failed.conflict.incoming_snapshot_ref).read_text())
    assert len(archived["facts"]["us-gaap"][tag]["units"]["USD"]) == 2


def test_new_accession_is_an_amendment_not_a_duplicate_conflict(batch):
    assert not ingest.run_ingestion(batch.settings).had_any_error
    payload = batch.connector.payloads[MIDDLE]
    recent = payload["submissions"]["filings"]["recent"]
    new_accession = "0000000002-24-000002"
    for values in recent.values():
        values.append(values[0])
    recent["accessionNumber"][-1] = new_accession
    recent["form"][-1] = "10-K/A"
    entries = payload["companyfacts"]["facts"]["us-gaap"]["Assets"]["units"]["USD"]
    entries.append(dict(entries[0], accn=new_accession, form="10-K/A", val=11))
    summary = ingest.run_ingestion(batch.settings)
    assert not summary.had_any_error
    assert summary.results[1].facts_written == summary.results[1].filings_written == 1
    facts = state(batch, MIDDLE)["facts"]
    assert {r.value for r in facts if r.metric_id == "us-gaap:Assets"} == {Decimal(10), Decimal(11)}


@pytest.mark.parametrize(
    "interruption",
    [KeyboardInterrupt, SystemExit, InterruptedError, MemoryError, asyncio.CancelledError],
)
def test_interruptions_propagate_and_roll_back_the_interrupted_company(batch, interruption):
    batch.connector.failures[MIDDLE] = interruption("simulated interruption")
    with pytest.raises(interruption):
        ingest.run_ingestion(batch.settings)
    assert batch.connector.calls == [c.entity_id for c in COMPANIES[:2]]
    assert len(state(batch, COMPANIES[0].entity_id)["facts"]) == 2
    assert all(not rows for rows in state(batch, MIDDLE).values())
    assert all(not rows for rows in state(batch, COMPANIES[2].entity_id).values())


def test_snapshot_interruption_is_not_converted_to_a_company_failure(batch, monkeypatch):
    write = ingest._write_new_file

    def interrupt_middle_snapshot(path, data):
        if MIDDLE in path.parts and path.name.startswith("companyfacts_"):
            raise InterruptedError("snapshot interrupted")
        return write(path, data)

    monkeypatch.setattr(ingest, "_write_new_file", interrupt_middle_snapshot)
    with pytest.raises(InterruptedError, match="snapshot interrupted"):
        ingest.run_ingestion(batch.settings)
    assert batch.connector.calls == [c.entity_id for c in COMPANIES[:2]]
    assert len(state(batch, COMPANIES[0].entity_id)["facts"]) == 2
    assert all(not rows for rows in state(batch, MIDDLE).values())
