"""The controlled Phase 1 test universe.

Exactly two companies, chosen for being large, stable, and well-documented
(low risk of an incorrect CIK), giving genuinely varied real data to test
against. This list must not be expanded or changed casually — doing so is
an explicit, deliberate decision (see DECISIONS.md), not a routine edit.
This remains the default when no companies/tickers are supplied. On-demand
ingestion resolves explicit tickers/CIKs through the archived SEC listings;
it does not expand or rewrite this controlled default list.
"""

from __future__ import annotations

from dataclasses import dataclass

from ibi.data_engine.sec_edgar.mapping import cik_to_entity_id


@dataclass(frozen=True, slots=True)
class FixedUniverseEntity:
    cik: int
    entity_id: str
    canonical_name: str
    ticker: str | None = None
    """Primary trading symbol, as listed in the company's SEC submissions."""


FIXED_UNIVERSE: tuple[FixedUniverseEntity, ...] = (
    FixedUniverseEntity(
        cik=320193, entity_id=cik_to_entity_id(320193), canonical_name="Apple Inc.",
        ticker="AAPL",
    ),
    FixedUniverseEntity(
        cik=789019, entity_id=cik_to_entity_id(789019),
        canonical_name="Microsoft Corporation", ticker="MSFT",
    ),
)
