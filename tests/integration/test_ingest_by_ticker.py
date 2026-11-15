"""On-demand ingestion: real transactions and bulk SQL, never external HTTP.

Reuses the existing migrated SQLite/PostgreSQL company-isolation harness.
Existing ingestion/integrity/source-integrity tests remain unchanged.
"""

import asyncio
import copy
import json
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from ibi.core.errors import ProviderError
from ibi.core.types import InsufficientEvidence, Unknown
from ibi.data_engine.sec_edgar import ingest, listings
from ibi.data_engine.sec_edgar.source_bytes import sha256_hex
from ibi.db.locks import entity_lock_key
from ibi.db.models import (
    EntityRecord,
    FinancialDataPointRecord,
    MetricGenerationRecord,
    SourceDocumentRecord,
)
from ibi.financial_engine import results_store as rs
from tests.integration import test_sec_edgar_integrity as harness
from tests.integration.test_sec_edgar_integrity import COMPANIES, MIDDLE, state

batch = harness.batch
PAIR = rs.VersionPair("1", "1")
PERIOD = (datetime(2023, 1, 1).date(), datetime(2023, 12, 31).date())


@pytest.fixture(autouse=True)
def no_external_http(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("on-demand tests must never make HTTP requests")

    monkeypatch.setattr("urllib.request.urlopen", forbidden)


def install_listings(batch):
    rows = [
        [c.cik, f"Listed company {c.cik}", ticker, "NYSE"]
        for c, ticker in zip(COMPANIES, ("ONE", "TWO", "THREE"), strict=True)
    ]
    rows += [[c.cik, f"Listed company {c.cik}", "AMB", None] for c in COMPANIES[:2]]
    raw = json.dumps({"fields": listings.FIELDS, "data": rows}).encode()
    fetched = listings.FetchedListings(raw, sha256_hex(raw), datetime.now(UTC))
    with Session(batch.engine) as session, session.begin():
        return listings.refresh_listings(session, Path(batch.settings.raw_data_dir), fetched)


def add_metric_inputs(batch):
    for company in COMPANIES:
        tags = batch.connector.payloads[company.entity_id]["companyfacts"]["facts"]["us-gaap"]
        for tag, value in (
            ("CostOfRevenue", 5),
            ("NetCashProvidedByUsedInOperatingActivities", 30),
            ("PaymentsToAcquirePropertyPlantAndEquipment", 7),
        ):
            tags[tag] = copy.deepcopy(tags["Revenues"])
            tags[tag]["units"]["USD"][0]["val"] = value


def large_company(batch):
    """5,000 facts AND 5,000 filings to expose both former N+1 loops."""
    body = batch.connector.payloads[COMPANIES[0].entity_id]
    recent = body["submissions"]["filings"]["recent"]
    for field, entries in recent.items():
        recent[field] = entries * 5000
    accessions = [f"0000000001-24-{index:06d}" for index in range(1, 5001)]
    recent["accessionNumber"] = accessions
    tags = body["companyfacts"]["facts"]["us-gaap"]
    instant = tags["Assets"]["units"]["USD"][0]
    duration = tags["Revenues"]["units"]["USD"][0]
    for tag, prototype, start in (("Assets", instant, 0), ("Revenues", duration, 2500)):
        tags[tag]["units"]["USD"] = [
            dict(prototype, accn=accession, val=index + 1)
            for index, accession in enumerate(accessions[start : start + 2500])
        ]


def test_5000_facts_and_filings_have_bounded_sql_and_are_idempotent(batch):
    large_company(batch)
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())
        if statement.lower().startswith("insert into financial_data"):
            assert not executemany  # Each captured batch is an actual multi-VALUES INSERT.

    event.listen(batch.engine, "before_cursor_execute", capture)
    try:
        first = ingest.run_ingestion(batch.settings, companies=[COMPANIES[0]])
        initial = list(statements)
        statements.clear()
        second = ingest.run_ingestion(batch.settings, companies=[COMPANIES[0]])
        repeated = list(statements)
    finally:
        event.remove(batch.engine, "before_cursor_execute", capture)
    assert not first.had_any_error and not second.had_any_error
    assert first.results[0].facts_written == first.results[0].filings_written == 5000
    assert second.results[0].facts_written == second.results[0].filings_written == 0
    assert second.results[0].facts_skipped_duplicate == 5000
    assert second.results[0].filings_skipped_existing == 5000
    assert len(initial) <= 30 and len(repeated) <= 7
    assert sum("from financial_data" in s for s in initial) == 1
    assert sum("from filings" in s for s in initial) == 1
    assert sum(s.startswith("insert into financial_data") for s in initial) == 10
    assert sum(s.startswith("insert into filings") for s in initial) == 10
    assert not any(
        s.startswith(("insert into financial_data", "insert into filings")) for s in repeated
    )
    assert len(state(batch, COMPANIES[0].entity_id)["facts"]) == 5000
    print(
        f"{batch.engine.dialect.name}: 5000 facts/5000 filings: "
        f"initial={len(initial)} <=30, repeated={len(repeated)} <=7"
    )


def test_failure_after_first_bulk_batch_rolls_back_all_writes_and_continues(batch):
    large_company(batch)
    with batch.engine.begin() as connection:
        if batch.engine.dialect.name == "postgresql":
            connection.execute(
                text(
                    "ALTER TABLE financial_data ADD CONSTRAINT reject_large_value "
                    "CHECK (value < 1000)"
                )
            )
        else:
            connection.execute(
                text(
                    "CREATE TRIGGER reject_large_value BEFORE INSERT ON financial_data "
                    "WHEN NEW.value >= 1000 BEGIN "
                    "SELECT RAISE(ABORT, 'large value rejected'); END"
                )
            )
    executed = []

    def track(connection, cursor, statement, parameters, context, executemany):
        if statement.lower().startswith("insert into financial_data"):
            executed.append(True)

    event.listen(batch.engine, "after_cursor_execute", track)
    try:
        summary = ingest.run_ingestion(batch.settings, companies=[COMPANIES[0], COMPANIES[2]])
    finally:
        event.remove(batch.engine, "after_cursor_execute", track)
    assert [r.status for r in summary.results] == ["failed", "success"]
    assert len(executed) >= 2  # First company's initial batch and the successful remaining company.
    failed = summary.results[0]
    assert failed.error_type == "IntegrityError" and not failed.ingestion_committed
    assert failed.facts_written == failed.filings_written == failed.source_documents_written == 0
    assert all(not rows for rows in state(batch, COMPANIES[0].entity_id).values())
    assert len(failed.raw_snapshot_refs) == 2
    assert all(Path(ref).is_file() for ref in failed.raw_snapshot_refs)
    assert len(state(batch, COMPANIES[2].entity_id)["facts"]) == 2


@pytest.mark.parametrize("incoming", [10, 20])
def test_existing_legacy_duplicate_values_are_not_lost_in_identity_cache(batch, incoming):
    company = COMPANIES[0]
    with Session(batch.engine) as session, session.begin():
        session.add(
            EntityRecord(entity_id=company.entity_id, kind="company", canonical_name="Legacy")
        )
        session.flush()
        for value in (10, 20):
            session.add(
                FinancialDataPointRecord(
                    entity_id=company.entity_id,
                    metric_id="us-gaap:Assets",
                    unit="USD",
                    start_date=None,
                    period_end_date=PERIOD[1],
                    value=Decimal(value),
                    epistemic_label="fact",
                    accession_number=None,
                )
            )
    before = state(batch, company.entity_id)
    entry = batch.connector.payloads[company.entity_id]["companyfacts"]["facts"]["us-gaap"][
        "Assets"
    ]["units"]["USD"][0]
    entry.update(accn=None, val=incoming)
    summary = ingest.run_ingestion(batch.settings, companies=[company])
    failed = summary.results[0]
    assert failed.error_type == "DuplicateFactConflict" and not failed.ingestion_committed
    assert failed.conflict.incoming_value == Decimal(incoming)
    assert failed.conflict.existing_value != failed.conflict.incoming_value
    assert failed.conflict.existing_fact_id in {r.id for r in before["facts"]}
    assert state(batch, company.entity_id) == before


def test_fetch_finishes_before_company_session_and_lock(batch, monkeypatch):
    install_listings(batch)
    active = 0
    scope = ingest.session_scope
    fetch = batch.connector.fetch
    archive = ingest._archive_raw_snapshot
    observations = []

    def probe(entity_id):
        if batch.engine.dialect.name != "postgresql":
            return True
        with batch.engine.connect() as connection:
            return connection.scalar(
                text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": entity_lock_key(entity_id)}
            )

    @contextmanager
    def tracked_scope(settings):
        nonlocal active
        active += 1
        try:
            with scope(settings) as session:
                yield session
        finally:
            active -= 1

    def checked_fetch(entity_id):
        assert active == 0
        assert probe(entity_id) is True
        records = fetch(entity_id)
        assert {r.payload["kind"] for r in records} == {"submissions", "companyfacts"}
        observations.append(entity_id)
        return records

    def checked_archive(data_dir, entity_id, *args, **kwargs):
        assert active == 1
        if batch.engine.dialect.name == "postgresql":
            assert probe(entity_id) is False  # Positive control: the lock is now held.
        return archive(data_dir, entity_id, *args, **kwargs)

    monkeypatch.setattr(ingest, "session_scope", tracked_scope)
    monkeypatch.setattr(batch.connector, "fetch", checked_fetch)
    monkeypatch.setattr(ingest, "_archive_raw_snapshot", checked_archive)
    summary = ingest.run_ingestion(batch.settings, tickers=["ONE", "TWO", "THREE"])
    assert not summary.had_any_error and observations == [c.entity_id for c in COMPANIES]


def test_exact_resolver_failures_are_per_ticker_and_successes_build_metrics(batch, monkeypatch):
    reference = install_listings(batch)
    add_metric_inputs(batch)

    def no_search(*args):
        pytest.fail("ingestion must never fall back to name search")

    monkeypatch.setattr(listings, "search_listings", no_search)
    requests = ["one", "AMB", "MISSING", "Listed company 1", "THREE", "CIK0000000002"]
    summary = ingest.run_ingestion(batch.settings, tickers=requests, build_metrics=True)
    assert [r.ticker for r in summary.results] == requests
    assert [r.status for r in summary.results] == [
        "success",
        "failed",
        "failed",
        "failed",
        "success",
        "success",
    ]
    assert [summary.results[i].error_type for i in (1, 2, 3)] == [
        "ConflictingEvidence",
        "Unknown",
        "Unknown",
    ]
    conflict = summary.results[1]
    assert conflict.entity_id is None and conflict.failure_stage == "resolution"
    assert {c.cik for c in conflict.resolution_evidence} == {1, 2}
    assert all(
        c.source_document_id == reference.source_document_id for c in conflict.resolution_evidence
    )
    assert batch.connector.calls == [COMPANIES[i].entity_id for i in (0, 2, 1)]
    for index in (0, 4, 5):
        outcome = summary.results[index]
        assert outcome.ingestion_committed and outcome.metrics_status == "success"
        assert outcome.listing_source_document_id == reference.source_document_id
        with Session(batch.engine) as session:
            entity = session.get(EntityRecord, outcome.entity_id)
            assert entity.canonical_name == f"Listed company {int(outcome.entity_id[3:])}"
            sources = session.scalars(
                select(SourceDocumentRecord).where(
                    SourceDocumentRecord.content_ref.contains(outcome.entity_id)
                )
            ).all()
            assert len(sources) == 2
            for source in sources:
                notes = json.loads(source.notes)
                assert notes["listing_source_document_id"] == reference.source_document_id
                assert notes["ticker_or_cik"] == outcome.ticker
                assert notes["resolved_cik"] == int(outcome.entity_id[3:])
                assert sha256_hex(Path(source.content_ref).read_bytes()) == source.content_sha256
            for metric, expected in (
                ("ibi:gross_margin", Decimal("0.75")),
                ("ibi:free_cash_flow", Decimal(23)),
            ):
                answer = rs.read_current(
                    session, outcome.entity_id, metric, *PERIOD, datetime.now(UTC), versions=PAIR
                )
                assert isinstance(answer, rs.ResolvedMetric) and answer.value == expected
                assert answer.generation_id == outcome.metric_builds[metric].generation_id
                assert answer.versions == PAIR and answer.basis_accession
                assert answer.evidence and all(e.fact_id for e in answer.evidence)
    assert summary.had_any_error


def test_existing_entity_name_is_never_overwritten_and_rebuild_is_idempotent(batch):
    install_listings(batch)
    add_metric_inputs(batch)
    with Session(batch.engine) as session, session.begin():
        session.add(
            EntityRecord(
                entity_id=COMPANIES[0].entity_id,
                kind="company",
                canonical_name="Original reviewed name",
            )
        )
    first = ingest.run_ingestion(batch.settings, tickers=["ONE"], build_metrics=True).results[0]
    second = ingest.run_ingestion(batch.settings, tickers=["ONE"], build_metrics=True).results[0]
    assert first.status == second.status == "success"
    assert second.facts_written == second.filings_written == 0
    assert all(outcome.status == "unchanged" for outcome in second.metric_builds.values())
    with Session(batch.engine) as session:
        assert session.get(EntityRecord, first.entity_id).canonical_name == "Original reviewed name"


def test_metric_failure_rolls_back_both_builds_but_retains_ingestion_and_continues(
    batch, monkeypatch
):
    add_metric_inputs(batch)
    build = rs.build_generation

    def fail_second_middle_build(session, entity_id, metric, pair):
        if entity_id == MIDDLE and metric == "ibi:free_cash_flow":
            session.execute(text("SELECT * FROM metric_build_probe_missing"))
        return build(session, entity_id, metric, pair)

    monkeypatch.setattr(rs, "build_generation", fail_second_middle_build)
    summary = ingest.run_ingestion(batch.settings, build_metrics=True)
    assert [r.status for r in summary.results] == ["success", "failed", "success"]
    failed = summary.results[1]
    assert failed.ingestion_committed and failed.failure_stage == "metrics"
    assert failed.metrics_status == "failed" and failed.facts_written == 5
    assert failed.error_type in ("ProgrammingError", "OperationalError") and failed.error
    assert not failed.metric_builds
    assert len(state(batch, MIDDLE)["facts"]) == 5
    with Session(batch.engine) as session:
        assert not session.scalars(
            select(MetricGenerationRecord).where(MetricGenerationRecord.entity_id == MIDDLE)
        ).all()
        assert len(session.scalars(select(MetricGenerationRecord)).all()) == 4


def test_metric_commit_error_cannot_undo_committed_facts(batch):
    add_metric_inputs(batch)

    def fail_middle_build_commit(session):
        if session.scalar(
            select(MetricGenerationRecord).where(MetricGenerationRecord.entity_id == MIDDLE)
        ):
            session.execute(text("SELECT * FROM metric_commit_probe_missing"))

    event.listen(Session, "before_commit", fail_middle_build_commit)
    try:
        summary = ingest.run_ingestion(batch.settings, build_metrics=True)
    finally:
        event.remove(Session, "before_commit", fail_middle_build_commit)
    failed = summary.results[1]
    assert failed.failure_stage == "metrics" and failed.ingestion_committed
    assert failed.metrics_status == "failed" and not failed.metric_builds
    assert len(state(batch, MIDDLE)["facts"]) == 5
    assert summary.results[2].status == "success"


@pytest.mark.parametrize(
    "interruption", [KeyboardInterrupt, InterruptedError, MemoryError, asyncio.CancelledError]
)
def test_metric_interruptions_propagate_without_undoing_ingestion(batch, monkeypatch, interruption):
    def interrupt(*args):
        raise interruption("metric interrupted")

    monkeypatch.setattr(rs, "build_generation", interrupt)
    with pytest.raises(interruption):
        ingest.run_ingestion(batch.settings, build_metrics=True)
    assert len(state(batch, COMPANIES[0].entity_id)["facts"]) == 2
    assert not state(batch, MIDDLE)["facts"]


def test_missing_metric_inputs_remain_uncertain_after_successful_build(batch):
    summary = ingest.run_ingestion(batch.settings, companies=[COMPANIES[0]], build_metrics=True)
    assert summary.results[0].metrics_status == "success"
    with Session(batch.engine) as session:
        for metric in summary.results[0].metric_builds:
            answer = rs.read_current(
                session, COMPANIES[0].entity_id, metric, *PERIOD, datetime.now(UTC), versions=PAIR
            )
            expected = InsufficientEvidence if metric == "ibi:gross_margin" else Unknown
            assert isinstance(answer, expected)


def test_fetch_and_resolver_exceptions_fail_only_their_request(batch, monkeypatch):
    install_listings(batch)
    resolve = listings.resolve_ticker

    def broken_lookup(session, query):
        if query == "LOOKUP-FAIL":
            raise RuntimeError("listing lookup failed")
        return resolve(session, query)

    monkeypatch.setattr(listings, "resolve_ticker", broken_lookup)
    batch.connector.failures[MIDDLE] = ProviderError("SEC unavailable")
    summary = ingest.run_ingestion(batch.settings, tickers=["ONE", "LOOKUP-FAIL", "TWO", "THREE"])
    assert [r.status for r in summary.results] == ["success", "failed", "failed", "success"]
    assert summary.results[1].failure_stage == "resolution"
    assert summary.results[2].failure_stage == "fetch"
    assert not state(batch, MIDDLE)["entities"]


def test_explicit_empty_company_list_does_not_fall_back_to_default(batch):
    assert ingest.run_ingestion(batch.settings, companies=[]).results == []
    assert batch.connector.calls == []


def test_prefetched_empty_response_cannot_become_a_successful_ingestion(batch):
    with pytest.raises(ProviderError, match="No SEC snapshots"):
        with Session(batch.engine) as session, session.begin():
            ingest.ingest_entity(
                session,
                batch.connector,
                COMPANIES[0],
                Path(batch.settings.raw_data_dir),
                raw_records=[],
            )
    assert not batch.connector.calls
    assert all(not rows for rows in state(batch, COMPANIES[0].entity_id).values())


def test_cli_ingests_exact_tickers_and_reports_failure_exit(batch, monkeypatch, capsys):
    install_listings(batch)
    add_metric_inputs(batch)
    monkeypatch.setattr(ingest, "get_settings", lambda: batch.settings)
    assert ingest.main(["--ticker", "ONE", "--ticker", "THREE"]) == 0
    output = capsys.readouterr().out
    assert "ONE: success" in output and "THREE: success" in output
    assert "ibi:gross_margin: created generation_id=" in output and "versions=1,1" in output
    assert ingest.main(["--ticker", "AMB", "--ticker", "TWO"]) == 1
    output = capsys.readouterr().out
    assert "AMB: failed" in output and "ConflictingEvidence" in output and "TWO: success" in output


def test_cli_without_tickers_keeps_the_default_universe(batch, monkeypatch, capsys):
    monkeypatch.setattr(ingest, "get_settings", lambda: batch.settings)
    assert ingest.main([]) == 0
    assert batch.connector.calls == [c.entity_id for c in COMPANIES]
