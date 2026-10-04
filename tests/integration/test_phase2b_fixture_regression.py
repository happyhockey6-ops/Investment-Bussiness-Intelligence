"""Phase 2B over the real (trimmed) AAPL/MSFT fixtures, end to end:
ingest -> build generations -> typed reads, plus a pure run of a test-only
single-tag metric over the one duration tag whose fixture history includes
real 8-K facts. The pinned outcome counts are a regression snapshot: if one
changes, review why before updating it.
"""

from __future__ import annotations

import dataclasses
from collections import Counter
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from ibi.core.types import UncertaintyReason
from ibi.data_engine.sec_edgar.fixed_universe import FixedUniverseEntity
from ibi.data_engine.sec_edgar.ingest import ingest_entity
from ibi.db.base import Base
from ibi.db.models import FilingRecord, FinancialDataPointRecord, MetricResultRecord
from ibi.financial_engine import results_store as rs
from ibi.financial_engine.formulas import FormulaSpec
from ibi.financial_engine.policy import POLICY_V1, ConceptSpec, MetricSpec
from ibi.financial_engine.resolver import FactView, compute_history
from tests.integration.test_sec_edgar_ingestion import FakeConnector, _load

AAPL = FixedUniverseEntity(cik=320193, entity_id="CIK0000320193", canonical_name="Apple Inc.")
MSFT = FixedUniverseEntity(cik=789019, entity_id="CIK0000789019", canonical_name="Microsoft")


@pytest.fixture
def ingested(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'regression.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        for entity, ticker in ((AAPL, "aapl"), (MSFT, "msft")):
            connector = FakeConnector(
                _load(f"{ticker}_submissions.json"), _load(f"{ticker}_companyfacts.json")
            )
            assert ingest_entity(s, connector, entity, tmp_path).error is None
        s.commit()
        yield s


def test_ingestion_persists_form_type_and_items(ingested):
    facts = ingested.execute(select(FinancialDataPointRecord)).scalars().all()
    assert facts and all(f.source_form_type for f in facts)
    filings = ingested.execute(select(FilingRecord)).scalars().all()
    assert filings and all(f.items is not None for f in filings)


def test_production_metrics_over_fixtures(ingested):
    """The trimmed fixtures carry revenue but no cost/cash-flow tags, so
    gross margin must be InsufficientEvidence(MISSING_INPUT) everywhere and
    free cash flow must have no series at all — never a fabricated number."""
    summary = Counter()
    for entity in (AAPL, MSFT):
        for metric in ("ibi:gross_margin", "ibi:free_cash_flow"):
            out = rs.build_generation(ingested, entity.entity_id, metric, rs.VersionPair("1", "1"))
            again = rs.build_generation(
                ingested, entity.entity_id, metric, rs.VersionPair("1", "1")
            )
            assert again.status == "unchanged"
            summary[(entity.entity_id, metric)] = out.rows_written
    ingested.commit()
    assert summary == PINNED_PRODUCTION_ROWS

    rows =ingested.execute(select(MetricResultRecord)).scalars().all()
    assert {(r.status, r.reason_code) for r in rows} == {
        ("insufficient_evidence", UncertaintyReason.MISSING_INPUT.value)
    }
    assert all(r.value is None for r in rows)


# Reviewed: AAPL has 11 non-YTD revenue periods (3 annual + 8 quarterly) and
# MSFT 25 (3 + 12 + 10, YTD excluded), one MISSING_INPUT epoch each; no fixture
# carries operating-cash-flow or capex tags, so free cash flow has no series.
PINNED_PRODUCTION_ROWS = Counter(
    {
        ("CIK0000320193", "ibi:gross_margin"): 11,
        ("CIK0000320193", "ibi:free_cash_flow"): 0,
        ("CIK0000789019", "ibi:gross_margin"): 25,
        ("CIK0000789019", "ibi:free_cash_flow"): 0,
    }
)


SBC_TAG = "us-gaap:AllocatedShareBasedCompensationExpense"
TEST_POLICY = dataclasses.replace(
    POLICY_V1,
    version="test-sbc",
    concepts=POLICY_V1.concepts + (ConceptSpec("sbc", (SBC_TAG,), "USD"),),
    metrics=(MetricSpec("test:sbc", "USD", ("quarter", "annual"), (("sbc", "sbc"),)),),
)
TEST_FORMULA = FormulaSpec(
    "test:sbc", "test", lambda x: x["sbc"], UncertaintyReason.ZERO_DENOMINATOR
)


def test_real_8k_facts_only_corroborate(ingested):
    rows = ingested.execute(
        select(FinancialDataPointRecord).where(
            FinancialDataPointRecord.entity_id == AAPL.entity_id,
            FinancialDataPointRecord.metric_id == SBC_TAG,
        )
    ).scalars()
    facts = [
        FactView(
            fact_id=r.id, metric_id=r.metric_id, unit=r.unit, start_date=r.start_date,
            end_date=r.period_end_date, value=Decimal(r.value),
            accession_number=r.accession_number, form_type=r.source_form_type,
            known_available_at=rs._utc(r.known_available_at),
            availability_precision=r.availability_precision,
        )
        for r in rows
    ]
    spec = TEST_POLICY.metric("test:sbc")
    history = compute_history(spec, TEST_POLICY, TEST_FORMULA, facts, [])
    statuses = Counter(h.resolution.status for h in history)
    corroborated_by_8k = sum(
        1 for h in history
        if any(
            e.relation == "corroborating" and e.form_type == "8-K" for e in h.resolution.evidence
        )
    )
    eight_k_bases = sum(
        1 for h in history
        if h.resolution.basis_accession
        and any(
            e.relation == "input" and e.form_type == "8-K" for e in h.resolution.evidence
        )
    )
    # Reviewed snapshot: 110 periodic + 3 8-K non-YTD facts, each a new epoch;
    # every real fixture 8-K equals the prior periodic value, so it only
    # corroborates — no 8-K is ever a basis, and nothing diverges.
    assert statuses == Counter({"value": 113})
    assert corroborated_by_8k == 3
    assert eight_k_bases == 0
