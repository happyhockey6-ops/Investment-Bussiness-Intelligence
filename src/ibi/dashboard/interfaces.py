"""The `DashboardQueryService` contract.

Deliberately read-only: the dashboard is a consumer of decisions, theses,
and evidence produced by the domain engines, never a place that computes or
overrides them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ibi.decision_engine.interfaces import Decision
from ibi.thesis_engine.interfaces import Thesis


class DashboardQueryService(ABC):
    """Not implemented in Phase 0."""

    @abstractmethod
    def get_decision(self, entity_id: str) -> Decision | None: ...

    @abstractmethod
    def get_thesis(self, entity_id: str) -> Thesis | None: ...
