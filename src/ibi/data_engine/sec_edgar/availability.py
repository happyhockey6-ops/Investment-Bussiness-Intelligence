"""The approved Phase 1 point-in-time availability rule.

`known_available_at` answers "when could this fact have legitimately been
used in a historical simulation" — never to be confused with `retrieval_date`
(when *we* fetched it) or `filing_date` (SEC's filing-date assignment,
which is not itself proof of dissemination). See DECISIONS.md, "Point-in-
time availability — hybrid rule," for the full review history and the
official SEC documentation this rule is grounded in.

Approved rule:
1. If `acceptance_date_time` is present, its America/New_York local time
   falls within EDGAR's documented operating hours (06:00-22:00), and that
   local date is an SEC/federal business day: use it directly
   (`availability_precision="acceptance_timestamp"`). SEC's own
   documentation ties acceptance and dissemination together as one
   automated pipeline within this window; outside it (e.g. an after-hours
   acceptance), SEC's documentation explicitly *decouples* them ("will not
   be disseminated by EDGAR until the next business day"), so this rule
   deliberately does not trust a raw timestamp outside the window.
2. Otherwise: 00:00 America/New_York on the next SEC/federal business day
   after `filing_date` (`availability_precision="day_conservative"`) — the
   previously-approved conservative fallback, used whenever precision
   cannot be documented as safe (field absent, off-hours, or any anomaly).

`retrieval_date` never participates in this computation, in any capacity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from ibi.data_engine.sec_edgar.holidays import is_sec_business_day, next_sec_business_day

_ET = ZoneInfo("America/New_York")
_UTC = ZoneInfo("UTC")
_OPERATING_START = time(6, 0)
_OPERATING_END = time(22, 0)

AvailabilityPrecision = str  # "acceptance_timestamp" | "day_conservative"


@dataclass(frozen=True, slots=True)
class Availability:
    known_available_at: datetime  # UTC, tz-aware
    availability_precision: AvailabilityPrecision


def compute_known_available_at(
    filing_date: date, acceptance_date_time: datetime | None
) -> Availability:
    if acceptance_date_time is not None:
        if acceptance_date_time.tzinfo is None:
            raise ValueError("acceptance_date_time must be timezone-aware")
        local = acceptance_date_time.astimezone(_ET)
        within_operating_hours = _OPERATING_START <= local.time() <= _OPERATING_END
        if within_operating_hours and is_sec_business_day(local.date()):
            return Availability(
                known_available_at=acceptance_date_time.astimezone(_UTC),
                availability_precision="acceptance_timestamp",
            )

    next_day = next_sec_business_day(filing_date)
    local_midnight = datetime.combine(next_day, time(0, 0), tzinfo=_ET)
    return Availability(
        known_available_at=local_midnight.astimezone(_UTC),
        availability_precision="day_conservative",
    )
