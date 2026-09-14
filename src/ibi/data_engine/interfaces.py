"""The `SourceConnector` contract.

Data integrity rule this enforces: raw ingested records are immutable once
stored (see `db.models.source_document` / `observation`). A connector
fetches and normalizes; it never mutates a previously stored record — a
correction arrives as a new, versioned record with its own
`ibi.core.epistemics.Provenance`, never as an in-place edit.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ibi.core.epistemics import Provenance


@dataclass(frozen=True, slots=True)
class RawRecord:
    entity_id: str
    payload: dict[str, object]
    provenance: Provenance


class SourceConnector(ABC):
    """Not implemented in Phase 0. One instance per external source
    (a filing feed, a news feed, a macro release calendar, ...)."""

    @property
    @abstractmethod
    def source_name(self) -> str: ...

    @abstractmethod
    def fetch(self, entity_id: str) -> list[RawRecord]:
        """Fetch and return raw, provenance-tagged records. Must not
        transform values in a way that loses traceability to the source."""
