"""Mapping tests against real, trimmed SEC fixture data (see
tests/fixtures/sec_edgar/) — no network access, no fabricated data."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from ibi.core.epistemics import EpistemicLabel
from ibi.data_engine.sec_edgar.mapping import (
    cik_to_entity_id,
    parse_companyfacts,
    parse_submissions,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "sec_edgar"


def _load(name: str) -> dict:
    with open(FIXTURES / name, encoding="utf-8") as f:
        return json.load(f)


def test_cik_to_entity_id_zero_pads():
    assert cik_to_entity_id(320193) == "CIK0000320193"
    assert cik_to_entity_id("320193") == "CIK0000320193"


def test_parse_submissions_produces_a_row_per_filing():
    raw = _load("aapl_submissions.json")
    rows = parse_submissions("CIK0000320193", raw)
    assert len(rows) == 6
    ten_ks = [r for r in rows if r.form_type == "10-K"]
    assert len(ten_ks) == 2
    for row in ten_ks:
        assert row.entity_id == "CIK0000320193"
        assert row.source == "sec_edgar"
        assert row.acceptance_date_time is not None
        # Real 10-Ks are always filed during normal business hours in this
        # fixture -> acceptance_timestamp precision, not the fallback.
        assert row.availability.availability_precision == "acceptance_timestamp"
        assert row.availability.known_available_at == row.acceptance_date_time


def test_parse_companyfacts_labels_every_fact_as_fact():
    raw = _load("aapl_companyfacts.json")
    rows = parse_companyfacts("CIK0000320193", raw)
    assert len(rows) > 0
    assert all(r.epistemic_label == EpistemicLabel.FACT for r in rows)


def test_parse_companyfacts_distinguishes_instant_and_duration_facts():
    raw = _load("aapl_companyfacts.json")
    rows = parse_companyfacts("CIK0000320193", raw)
    assets_rows = [r for r in rows if r.metric_id == "us-gaap:Assets"]
    revenue_rows = [r for r in rows if r.metric_id == "us-gaap:Revenues"]
    assert assets_rows and all(r.start_date is None for r in assets_rows)  # instant
    assert revenue_rows and all(r.start_date is not None for r in revenue_rows)  # duration


def test_parse_companyfacts_preserves_the_real_start_date_collision_case():
    """AllocatedShareBasedCompensationExpense ending 2013-06-29 under accession
    0001193125-14-277160 has two genuinely different values distinguished
    only by start_date — this is the real collision found during empirical
    validation that justified adding start_date to the identity key."""
    raw = _load("aapl_companyfacts.json")
    rows = parse_companyfacts("CIK0000320193", raw)
    matches = [
        r
        for r in rows
        if r.metric_id == "us-gaap:AllocatedShareBasedCompensationExpense"
        and r.end_date == date(2013, 6, 29)
        and r.accession_number == "0001193125-14-277160"
    ]
    assert len(matches) == 2
    starts = {r.start_date for r in matches}
    values = {r.value for r in matches}
    assert starts == {date(2012, 9, 30), date(2013, 3, 31)}
    assert values == {Decimal("1698000000"), Decimal("578000000")}


def test_parse_companyfacts_uses_filing_lookup_when_available():
    submissions = _load("aapl_submissions.json")
    companyfacts = _load("aapl_companyfacts.json")
    filings = parse_submissions("CIK0000320193", submissions)
    lookup = {f.accession_number: f for f in filings}

    without_lookup = parse_companyfacts("CIK0000320193", companyfacts, filing_lookup=None)
    with_lookup = parse_companyfacts("CIK0000320193", companyfacts, filing_lookup=lookup)

    # Facts whose accession IS in the trimmed submissions fixture should
    # gain acceptance_timestamp precision when the lookup is provided;
    # facts from older accessions not in the "recent" window are unaffected.
    known_accessions = set(lookup.keys())
    upgraded = [
        (a, b)
        for a, b in zip(without_lookup, with_lookup, strict=True)
        if a.accession_number in known_accessions
    ]
    assert upgraded, "fixture should contain at least one fact from a known accession"
    for _without, with_ in upgraded:
        assert with_.availability.availability_precision == "acceptance_timestamp"
