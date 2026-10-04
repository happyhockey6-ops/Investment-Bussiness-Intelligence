"""Synthetic fact builders shared by the pure Phase 2B resolver tests."""

from __future__ import annotations

import itertools
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from ibi.financial_engine.resolver import FactView, FilingEvent

FY23 = (date(2023, 1, 1), date(2023, 12, 31))
T0 = datetime(2024, 2, 1, 15, 0, tzinfo=UTC)

REV = "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
REV_ALT = "us-gaap:Revenues"
COST = "us-gaap:CostOfGoodsAndServicesSold"
GP = "us-gaap:GrossProfit"

_ids = itertools.count(1)


def at(days: float) -> datetime:
    return T0 + timedelta(days=days)


def fact(
    tag: str,
    value: str | int,
    accn: str,
    form: str | None,
    when: datetime = T0,
    period: tuple[date, date] = FY23,
    unit: str = "USD",
    precision: str = "acceptance_timestamp",
) -> FactView:
    return FactView(
        fact_id=next(_ids),
        metric_id=tag,
        unit=unit,
        start_date=period[0],
        end_date=period[1],
        value=Decimal(str(value)),
        accession_number=accn,
        form_type=form,
        known_available_at=when,
        availability_precision=precision,
    )


def gm_filing(accn: str, form: str | None, rev, cost, when: datetime = T0, **kw) -> list[FactView]:
    return [fact(REV, rev, accn, form, when, **kw), fact(COST, cost, accn, form, when, **kw)]


def event_8k(accn: str, items: str, when: datetime, filing_date: date) -> FilingEvent:
    return FilingEvent(
        accession_number=accn,
        form_type="8-K",
        items=items,
        filing_date=filing_date,
        known_available_at=when,
        availability_precision="acceptance_timestamp",
    )
