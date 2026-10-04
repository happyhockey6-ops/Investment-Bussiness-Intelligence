"""Approved Phase 2B validation (C6): run the resolver over the FULL live SEC
data for the fixed universe (AAPL, MSFT), entirely in memory — nothing is
written to any database or to disk. Opt-in only:

    pytest -m live_network -s tests/integration/test_phase2b_full_data_live.py

Requires IBI_SEC_USER_AGENT (a real contact identifier, per SEC policy).
Prints a per-entity, per-metric summary of statuses and reason codes for
review; asserts only integrity properties (forms consistent between SEC
sources, results independent of input order, no 8-K ever a basis).
"""

from __future__ import annotations

from collections import Counter

import pytest

from ibi.config import get_settings
from ibi.data_engine.sec_edgar.client import SecHttpClient, SecHttpClientConfig
from ibi.data_engine.sec_edgar.connector import SecEdgarConnector
from ibi.data_engine.sec_edgar.fixed_universe import FIXED_UNIVERSE
from ibi.data_engine.sec_edgar.mapping import parse_companyfacts, parse_submissions
from ibi.financial_engine.formulas import FORMULAS
from ibi.financial_engine.policy import POLICY_V1
from ibi.financial_engine.resolver import FactView, FilingEvent, compute_history

pytestmark = pytest.mark.live_network


@pytest.mark.parametrize("entity", FIXED_UNIVERSE, ids=lambda e: e.entity_id)
def test_full_history_in_memory(entity):
    settings = get_settings()
    client = SecHttpClient(SecHttpClientConfig(user_agent=settings.require_sec_user_agent()))
    bodies = {
        r.payload["kind"]: r.payload["body"]
        for r in SecEdgarConnector(client).fetch(entity.entity_id)
    }
    filings = parse_submissions(entity.entity_id, bodies["submissions"])
    lookup = {f.accession_number: f for f in filings}
    # Raises FormTypeMismatchError on any companyfacts/submissions disagreement.
    rows = parse_companyfacts(entity.entity_id, bodies["companyfacts"], lookup)

    facts = [
        FactView(
            fact_id=None, metric_id=r.metric_id, unit=r.unit, start_date=r.start_date,
            end_date=r.end_date, value=r.value, accession_number=r.accession_number,
            form_type=r.form_type, known_available_at=r.availability.known_available_at,
            availability_precision=r.availability.availability_precision,
        )
        for r in rows
    ]
    events = [
        FilingEvent(
            accession_number=f.accession_number, form_type=f.form_type, items=f.items,
            filing_date=f.filing_date, known_available_at=f.availability.known_available_at,
            availability_precision=f.availability.availability_precision,
        )
        for f in filings
    ]

    for spec in POLICY_V1.metrics:
        formula = FORMULAS[(spec.metric_id, "1")]
        history = compute_history(spec, POLICY_V1, formula, facts, events)
        reversed_history = compute_history(
            spec, POLICY_V1, formula, list(reversed(facts)), list(reversed(events))
        )
        assert [h.resolution.result_hash for h in history] == [
            h.resolution.result_hash for h in reversed_history
        ]
        assert not any(
            e.relation == "input" and e.form_type in POLICY_V1.recast_forms
            for h in history for e in h.resolution.evidence
        )
        summary = Counter((h.resolution.status, h.resolution.reason_code) for h in history)
        periods = {(h.start_date, h.end_date) for h in history}
        print(
            f"\n{entity.entity_id} {spec.metric_id}: {len(periods)} periods, "
            f"{len(history)} epochs, outcomes={dict(summary)}"
        )
