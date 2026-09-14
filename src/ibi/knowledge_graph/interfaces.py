"""The `RelationshipStore` contract.

A relational database (see `db/`) is the Phase 0 persistence foundation;
whether the knowledge graph eventually needs a dedicated graph database is
an open question (see DECISIONS.md) deferred until real query patterns exist.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ibi.core.epistemics import Provenance


@dataclass(frozen=True, slots=True)
class Relationship:
    source_entity_id: str
    target_entity_id: str
    relationship_type: str  # e.g. "supplier_of", "competitor_of", "executive_of"
    provenance: Provenance


class RelationshipStore(ABC):
    """Not implemented in Phase 0."""

    @abstractmethod
    def add(self, relationship: Relationship) -> None: ...

    @abstractmethod
    def related_to(
        self, entity_id: str, relationship_type: str | None = None
    ) -> list[Relationship]: ...
