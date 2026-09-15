"""End-to-end SEC EDGAR ingestion tests against real, trimmed fixture data
and a temporary SQLite database. No network access — a `FakeConnector`
returns fixture bytes exactly as `SecEdgarConnector.fetch` would, except one
test that monkeypatches `urllib.request.urlopen` to exercise the real HTTP
client/connector code paths (still without real network access) for the
partial-failure-isolation behavior of `run_ingestion` itself.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ibi.core.epistemics import Provenance, SourceTier
from ibi.data_engine.interfaces import RawRecord
from ibi.data_engine.sec_edgar.fixed_universe import FixedUniverseEntity
from ibi.data_engine.sec_edgar.ingest import ingest_entity
from ibi.db.base import Base
from ibi.db.models import FilingRecord, FinancialDataPointRecord

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "sec_edgar"
AAPL = FixedUniverseEntity(cik=320193, entity_id="CIK0000320193", canonical_name="Apple Inc.")


def _load(name: str) -> dict:
    with open(FIXTURES / name, encoding="utf-8") as f:
        return json.load(f)


class FakeConnector:
    """Returns fixture bytes with the exact payload shape
    `SecEdgarConnector.fetch` produces, with no network access."""

    def __init__(self, submissions: dict, companyfacts: dict):
        self._submissions = submissions
        self._companyfacts = companyfacts

    @property
    def source_name(self) -> str:
        return "sec_edgar"

    def fetch(self, entity_id: str) -> list[RawRecord]:
        now = datetime.now(UTC)
        submissions_url = "https://data.sec.gov/submissions/x.json"
        companyfacts_url = "https://data.sec.gov/api/xbrl/companyfacts/x.json"

        def _provenance(url: str) -> Provenance:
            return Provenance(
                source="SEC EDGAR",
                source_tier=SourceTier.PRIMARY_REGULATORY,
                retrieval_date=now,
                source_url=url,
            )

        return [
            RawRecord(
                entity_id=entity_id,
                payload={"kind": "submissions", "url": submissions_url, "body": self._submissions},
                provenance=_provenance(submissions_url),
            ),
            RawRecord(
                entity_id=entity_id,
                payload={
                    "kind": "companyfacts",
                    "url": companyfacts_url,
                    "body": self._companyfacts,
                },
                provenance=_provenance(companyfacts_url),
            ),
        ]


@pytest.fixture
def sqlite_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def aapl_connector():
    return FakeConnector(_load("aapl_submissions.json"), _load("aapl_companyfacts.json"))


def test_full_pipeline_writes_entities_filings_and_facts(sqlite_session, aapl_connector, tmp_path):
    result = ingest_entity(sqlite_session, aapl_connector, AAPL, tmp_path)
    sqlite_session.commit()

    assert result.source_documents_written == 2
    assert result.filings_written == 6  # trimmed fixture has 6 filings
    assert result.facts_written > 0
    assert result.error is None

    filings = sqlite_session.execute(select(FilingRecord)).scalars().all()
    assert len(filings) == 6
    facts = sqlite_session.execute(select(FinancialDataPointRecord)).scalars().all()
    assert len(facts) == result.facts_written
    assert all(f.epistemic_label == "fact" for f in facts)
    assert all(f.entity_id == "CIK0000320193" for f in facts)


def test_ingestion_is_idempotent(sqlite_session, aapl_connector, tmp_path):
    first = ingest_entity(sqlite_session, aapl_connector, AAPL, tmp_path)
    sqlite_session.commit()

    second = ingest_entity(sqlite_session, aapl_connector, AAPL, tmp_path)
    sqlite_session.commit()

    assert second.filings_written == 0
    assert second.filings_skipped_existing == first.filings_written
    assert second.facts_written == 0
    assert second.facts_skipped_duplicate == first.facts_written

    total_facts = sqlite_session.execute(select(FinancialDataPointRecord)).scalars().all()
    assert len(total_facts) == first.facts_written  # not doubled


def test_known_available_at_supports_point_in_time_filtering(
    sqlite_session, aapl_connector, tmp_path
):
    ingest_entity(sqlite_session, aapl_connector, AAPL, tmp_path)
    sqlite_session.commit()

    all_facts = sqlite_session.execute(select(FinancialDataPointRecord)).scalars().all()
    distinct_accessions = {f.accession_number for f in all_facts}
    assert len(distinct_accessions) > 1, "fixture must span multiple filings for a meaningful test"

    # Simulate a backtest "as_of" set to the earliest known_available_at
    # among all ingested facts: only facts from that same earliest moment
    # should be usable — anything from a later filing must be excluded.
    earliest = min(f.known_available_at for f in all_facts)
    query = select(FinancialDataPointRecord).where(
        FinancialDataPointRecord.known_available_at <= earliest
    )
    usable_as_of_earliest = sqlite_session.execute(query).scalars().all()
    assert all(f.known_available_at <= earliest for f in usable_as_of_earliest)
    # Most facts are NOT yet available at the earliest moment.
    assert len(usable_as_of_earliest) < len(all_facts)


def test_database_enforces_duration_fact_identity(sqlite_session):
    """Two duration facts differing only by value (a would-be accidental
    duplicate) must be rejected by the DB-level partial unique index, not
    just application dedup logic."""
    from datetime import date
    from decimal import Decimal

    from ibi.db.models import EntityRecord

    sqlite_session.add(
        EntityRecord(entity_id="CIK0000000001", kind="company", canonical_name="Test Co")
    )
    sqlite_session.flush()

    common = dict(
        entity_id="CIK0000000001",
        metric_id="us-gaap:Revenues",
        unit="USD",
        start_date=date(2020, 1, 1),
        period_end_date=date(2020, 12, 31),
        epistemic_label="fact",
        accession_number="0000000001-20-000001",
    )
    sqlite_session.add(FinancialDataPointRecord(value=Decimal("100"), **common))
    sqlite_session.commit()

    # Same identity key as the row above -> must violate the DB constraint.
    sqlite_session.add(FinancialDataPointRecord(value=Decimal("999"), **common))
    with pytest.raises(IntegrityError):
        sqlite_session.commit()
    sqlite_session.rollback()


def test_database_allows_duration_and_instant_facts_to_coexist(sqlite_session):
    """A duration fact and an instant fact sharing every other column must
    NOT collide with each other (different partial indexes govern them)."""
    from datetime import date
    from decimal import Decimal

    from ibi.db.models import EntityRecord

    sqlite_session.add(
        EntityRecord(entity_id="CIK0000000002", kind="company", canonical_name="Test Co 2")
    )
    sqlite_session.flush()

    sqlite_session.add(
        FinancialDataPointRecord(
            entity_id="CIK0000000002", metric_id="us-gaap:Assets", unit="USD",
            start_date=date(2020, 1, 1), period_end_date=date(2020, 12, 31),
            value=Decimal("100"), epistemic_label="fact", accession_number="acc-1",
        )
    )
    sqlite_session.add(
        FinancialDataPointRecord(
            entity_id="CIK0000000002", metric_id="us-gaap:Assets", unit="USD",
            start_date=None, period_end_date=date(2020, 12, 31),
            value=Decimal("200"), epistemic_label="fact", accession_number="acc-1",
        )
    )
    sqlite_session.commit()  # must not raise
    query = select(FinancialDataPointRecord).where(
        FinancialDataPointRecord.entity_id == "CIK0000000002"
    )
    facts = sqlite_session.execute(query).scalars().all()
    assert len(facts) == 2


def test_run_ingestion_isolates_entity_failures(monkeypatch, tmp_path):
    """One entity's HTTP failure must not prevent another entity's data
    from being ingested and persisted."""
    import urllib.error
    import urllib.request

    from ibi.config import get_settings

    db_path = tmp_path / "isolation_test.db"
    monkeypatch.setenv("IBI_DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("IBI_SEC_USER_AGENT", "Test Suite test@example.com")
    monkeypatch.setenv("IBI_RAW_DATA_DIR", str(tmp_path / "raw"))
    get_settings.cache_clear()
    settings = get_settings()

    engine = create_engine(settings.database_url)
    Base.metadata.create_all(engine)

    aapl_submissions = _load("aapl_submissions.json")
    aapl_companyfacts = _load("aapl_companyfacts.json")

    def fake_urlopen(request, timeout=None):
        url = request.full_url
        if "0000789019" in url:  # MSFT: simulate a hard network failure
            raise urllib.error.URLError("simulated network failure")
        if "submissions" in url:
            body = json.dumps(aapl_submissions).encode()
        else:
            body = json.dumps(aapl_companyfacts).encode()

        class _Resp:
            def read(self_inner):
                return body

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False

        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    from ibi.data_engine.sec_edgar.ingest import run_ingestion

    summary = run_ingestion(settings)

    assert len(summary.results) == 2
    by_entity = {r.entity_id: r for r in summary.results}
    assert by_entity["CIK0000320193"].error is None
    assert by_entity["CIK0000320193"].facts_written > 0
    assert by_entity["CIK0000789019"].error is not None

    with Session(engine) as session:
        query = select(FinancialDataPointRecord).where(
            FinancialDataPointRecord.entity_id == "CIK0000320193"
        )
        aapl_facts = session.execute(query).scalars().all()
        assert len(aapl_facts) > 0  # AAPL's data survived MSFT's failure
