"""Federal holiday computation must match known real dates, and must
correctly apply the weekend-observance shift and Juneteenth's 2021 start."""

from __future__ import annotations

from datetime import date

from ibi.data_engine.sec_edgar.holidays import (
    federal_holidays,
    is_sec_business_day,
    next_sec_business_day,
)


def test_known_2026_holiday_dates():
    holidays = federal_holidays(2026)
    assert date(2026, 1, 1) in holidays  # New Year's Day
    assert date(2026, 1, 19) in holidays  # MLK Day: 3rd Monday of Jan 2026
    assert date(2026, 11, 26) in holidays  # Thanksgiving: 4th Thursday of Nov 2026


def test_independence_day_weekend_shift_2026():
    # July 4, 2026 is a Saturday -> observed Friday July 3, 2026.
    assert date(2026, 7, 4).weekday() == 5
    holidays = federal_holidays(2026)
    assert date(2026, 7, 3) in holidays
    assert date(2026, 7, 4) not in holidays


def test_juneteenth_not_a_holiday_before_2021():
    assert date(2020, 6, 19) not in federal_holidays(2020)
    assert date(2021, 6, 18) in federal_holidays(2021)  # 2021: June 19 was a Saturday


def test_is_sec_business_day_rejects_weekends_and_holidays():
    assert not is_sec_business_day(date(2026, 1, 1))  # New Year's Day (Thursday)
    assert not is_sec_business_day(date(2026, 1, 3))  # Saturday
    assert not is_sec_business_day(date(2026, 1, 4))  # Sunday
    assert is_sec_business_day(date(2026, 1, 2))  # ordinary Friday


def test_next_sec_business_day_skips_weekend():
    friday = date(2026, 1, 2)
    assert next_sec_business_day(friday) == date(2026, 1, 5)  # Monday


def test_next_sec_business_day_skips_weekend_and_holiday():
    # Friday Nov 27, 2026 is not a holiday itself, but test a Friday
    # immediately before a Monday holiday: MLK Day 2027 is Mon Jan 18.
    friday_before_holiday = date(2027, 1, 15)
    assert is_sec_business_day(friday_before_holiday)
    next_day = next_sec_business_day(friday_before_holiday)
    assert next_day == date(2027, 1, 19)  # Tue, skipping Sat/Sun/MLK-Mon
