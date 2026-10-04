"""Phase 2B mapping additions: per-fact form type, 8-K item codes, and the
companyfacts-vs-submissions form consistency check."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ibi.data_engine.sec_edgar.mapping import (
    FormTypeMismatchError,
    parse_companyfacts,
    parse_submissions,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "sec_edgar"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("ticker", ["aapl", "msft"])
def test_every_fixture_fact_carries_its_form(ticker):
    filings = parse_submissions("E", _load(f"{ticker}_submissions.json"))
    facts = parse_companyfacts(
        "E", _load(f"{ticker}_companyfacts.json"), {f.accession_number: f for f in filings}
    )
    assert facts and all(f.form_type for f in facts)
    assert any(f.form_type == "8-K" for f in facts)


def test_items_are_parsed_from_submissions():
    raw = _load("aapl_submissions.json")
    rows = parse_submissions("E", raw)
    assert [r.items for r in rows] == raw["filings"]["recent"]["items"]


def test_items_absent_from_response_stay_none():
    raw = _load("aapl_submissions.json")
    del raw["filings"]["recent"]["items"]
    assert all(r.items is None for r in parse_submissions("E", raw))


def test_form_mismatch_between_sources_is_an_integrity_error():
    filings = parse_submissions("E", _load("aapl_submissions.json"))
    lookup = {f.accession_number: f for f in filings}
    raw = _load("aapl_companyfacts.json")
    entries = [
        entry
        for tags in raw["facts"].values()
        for tag_data in tags.values()
        for unit_entries in tag_data["units"].values()
        for entry in unit_entries
    ]
    target = next(e["accn"] for e in entries if e["accn"] in lookup)
    for entry in entries:
        if entry["accn"] == target:
            entry["form"] = "8-K"
    with pytest.raises(FormTypeMismatchError):
        parse_companyfacts("E", raw, lookup)
