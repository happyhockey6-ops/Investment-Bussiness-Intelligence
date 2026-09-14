"""The `EventStore` contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from ibi.core.epistemics import Provenance


@dataclass(frozen=True, slots=True)
class Event:
    event_id: str
    entity_id: str
    kind: str
    occurred_at: datetime
    description: str
    provenance: Provenance


class EventStore(ABC):
    """Not implemented in Phase 0."""

    @abstractmethod
    def record(self, event: Event) -> None:
        """Append-only: events are never edited in place, only superseded
        by a new event referencing the old one, to preserve history."""

    @abstractmethod
    def for_entity(self, entity_id: str) -> list[Event]: ...
