"""Opt-in, real-network SEC EDGAR test for a single company (Apple, CIK
0000320193) — never run by default (see the `live_network` marker and
`addopts` in pyproject.toml). Run explicitly with:

    pytest -m live_network tests/integration/test_sec_edgar_live.py

Requires IBI_SEC_USER_AGENT to be set to a real contact identifier.
"""

from __future__ import annotations

import pytest

from ibi.config import get_settings
from ibi.core.types import Unknown
from ibi.data_engine.sec_edgar.client import SecHttpClient, SecHttpClientConfig
from ibi.data_engine.sec_edgar.connector import SecEdgarConnector
from ibi.data_engine.sec_edgar.mapping import parse_companyfacts, parse_submissions

pytestmark = pytest.mark.live_network

APPLE_ENTITY_ID = "CIK0000320193"


def test_fetch_and_parse_real_apple_filings():
    settings = get_settings()
    client = SecHttpClient(SecHttpClientConfig(user_agent=settings.require_sec_user_agent()))
    connector = SecEdgarConnector(client)

    records = connector.fetch(APPLE_ENTITY_ID)
    kinds = {r.payload["kind"] for r in records}
    assert kinds == {"submissions", "companyfacts"}

    bodies = {r.payload["kind"]: r.payload["body"] for r in records}
    submissions_body = bodies["submissions"]
    companyfacts_body = bodies["companyfacts"]

    assert not isinstance(submissions_body, Unknown)
    assert "Apple" in submissions_body["name"]
    assert submissions_body["cik"] == "320193" or int(submissions_body["cik"]) == 320193

    filings = parse_submissions(APPLE_ENTITY_ID, submissions_body)
    assert len(filings) > 0
    assert any(f.form_type == "10-K" for f in filings)

    lookup = {f.accession_number: f for f in filings}
    facts = parse_companyfacts(APPLE_ENTITY_ID, companyfacts_body, filing_lookup=lookup)
    assert len(facts) > 100  # Apple has a large XBRL footprint
    assert all(f.entity_id == APPLE_ENTITY_ID for f in facts)
