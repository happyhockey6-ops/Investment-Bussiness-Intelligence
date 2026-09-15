"""US federal holiday calendar and SEC/EDGAR business-day helpers.

SEC EDGAR's operating calendar follows federal government holidays (SEC is
a federal agency) — per SEC's own EDGAR Calendar page, EDGAR "will not
receive, process, or accept filings in observance of the following federal
holidays." This is deliberately the *federal* calendar, not the NYSE market
calendar (the federal calendar observes Columbus Day and Veterans Day; NYSE
does not) — Phase 1's point-in-time rule reasons about SEC/EDGAR
dissemination, not market trading days.

Holidays are computed from their defining rule (nth weekday of a month, or
a fixed date with the standard weekend-observance shift) rather than
hand-enumerated per year, to avoid transcription error across the multi
-decade range ingested SEC data can span.
"""

from __future__ import annotations

from datetime import date, timedelta


def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
    """`weekday`: Monday=0 ... Sunday=6. `n` is 1-indexed (1st, 2nd, ...)."""
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    d += timedelta(days=offset)
    d += timedelta(weeks=n - 1)
    return d


def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    d = next_month - timedelta(days=1)
    offset = (d.weekday() - weekday) % 7
    return d - timedelta(days=offset)


def _observed(d: date) -> date:
    """Standard federal weekend-observance shift: Saturday -> preceding
    Friday, Sunday -> following Monday."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def federal_holidays(year: int) -> set[date]:
    """US federal holidays SEC/EDGAR observes for the given year."""
    holidays = {
        _observed(date(year, 1, 1)),           # New Year's Day
        _nth_weekday_of_month(year, 1, 0, 3),  # MLK Day (3rd Mon Jan)
        _nth_weekday_of_month(year, 2, 0, 3),  # Washington's Birthday (3rd Mon Feb)
        _last_weekday_of_month(year, 5, 0),    # Memorial Day (last Mon May)
        _observed(date(year, 7, 4)),           # Independence Day
        _nth_weekday_of_month(year, 9, 0, 1),  # Labor Day (1st Mon Sep)
        _nth_weekday_of_month(year, 10, 0, 2), # Columbus Day (2nd Mon Oct)
        _observed(date(year, 11, 11)),         # Veterans Day
        _nth_weekday_of_month(year, 11, 3, 4), # Thanksgiving (4th Thu Nov)
        _observed(date(year, 12, 25)),         # Christmas Day
    }
    if year >= 2021:
        holidays.add(_observed(date(year, 6, 19)))  # Juneteenth (federal holiday since 2021)
    return holidays


def is_sec_business_day(d: date) -> bool:
    """True if EDGAR is open: Monday-Friday, not a federal holiday."""
    if d.weekday() >= 5:
        return False
    return d not in federal_holidays(d.year)


def next_sec_business_day(d: date) -> date:
    """The next SEC/EDGAR business day strictly after `d`."""
    candidate = d + timedelta(days=1)
    while not is_sec_business_day(candidate):
        candidate += timedelta(days=1)
    return candidate
