"""The approved hybrid known_available_at rule: acceptance timestamp when
demonstrably safe, conservative business-day fallback otherwise —
retrieval_date must never be involved (it isn't even a parameter here)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from ibi.data_engine.sec_edgar.availability import compute_known_available_at


def test_acceptance_within_operating_hours_on_business_day_is_used_directly():
    # Real example from empirical validation: AAPL 10-K accepted 2025-10-31T10:01:26Z.
    accepted = datetime(2025, 10, 31, 10, 1, 26, tzinfo=UTC)
    result = compute_known_available_at(
        filing_date=date(2025, 10, 31), acceptance_date_time=accepted
    )
    assert result.availability_precision == "acceptance_timestamp"
    assert result.known_available_at == accepted


def test_acceptance_outside_operating_hours_falls_back_to_conservative_rule():
    # 2019-10-31 23:31 ET (well after the 22:00 ET operating-hours cutoff)
    # -> 03:31 UTC on 2019-11-01, outside the documented safe window.
    accepted = datetime(2019, 11, 1, 3, 31, 0, tzinfo=UTC)
    # 2019-10-31 is a Thursday -> next business day is Friday 2019-11-01.
    result = compute_known_available_at(
        filing_date=date(2019, 10, 31), acceptance_date_time=accepted
    )
    assert result.availability_precision == "day_conservative"
    assert result.known_available_at.astimezone(UTC).date() == date(2019, 11, 1)


def test_missing_acceptance_datetime_falls_back_to_conservative_rule():
    result = compute_known_available_at(filing_date=date(2024, 3, 15), acceptance_date_time=None)
    assert result.availability_precision == "day_conservative"


def test_friday_filing_conservative_fallback_lands_on_monday():
    # Friday with no acceptance timestamp at all.
    result = compute_known_available_at(filing_date=date(2026, 1, 2), acceptance_date_time=None)
    # 2026-01-02 is a Friday; next business day is Monday 2026-01-05, 00:00 ET.
    expected_utc = datetime(2026, 1, 5, 5, 0, tzinfo=UTC)  # ET is UTC-5 in January
    assert result.known_available_at == expected_utc


def test_naive_acceptance_datetime_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        compute_known_available_at(
            filing_date=date(2024, 1, 1), acceptance_date_time=datetime(2024, 1, 1, 10, 0)
        )


def test_retrieval_date_is_not_a_parameter_of_this_function():
    import inspect

    params = inspect.signature(compute_known_available_at).parameters
    assert "retrieval_date" not in params
