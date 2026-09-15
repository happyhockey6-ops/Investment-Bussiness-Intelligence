"""The controlled Phase 1 test universe.

Exactly two companies, chosen for being large, stable, and well-documented
(low risk of an incorrect CIK), giving genuinely varied real data to test
against. This list must not be expanded or changed casually — doing so is
an explicit, deliberate decision (see DECISIONS.md), not a routine edit.
Phase 1 has no general entity resolver; this is the entire universe.
"""

from __future__ import annotations

from dataclasses import dataclass

from ibi.data_engine.sec_edgar.mapping import cik_to_entity_id


@dataclass(frozen=True, slots=True)
class FixedUniverseEntity:
    cik: int
    entity_id: str
    canonical_name: str


FIXED_UNIVERSE: tuple[FixedUniverseEntity, ...] = (
    FixedUniverseEntity(
        cik=320193, entity_id=cik_to_entity_id(320193), canonical_name="Apple Inc."
    ),
    FixedUniverseEntity(
        cik=789019, entity_id=cik_to_entity_id(789019), canonical_name="Microsoft Corporation"
    ),
)
