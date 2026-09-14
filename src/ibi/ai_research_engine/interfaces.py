"""The `ResearchTask` contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ibi.core.epistemics import Evidence
from ibi.providers.ai.base import ModelTier


@dataclass(frozen=True, slots=True)
class ResearchQuestion:
    entity_id: str
    question: str
    tier: ModelTier


@dataclass(frozen=True, slots=True)
class ResearchFinding:
    question: ResearchQuestion
    answer: str
    supporting_evidence: tuple[Evidence, ...]
    confidence: float


class ResearchTask(ABC):
    """Not implemented in Phase 0."""

    @abstractmethod
    def run(self, question: ResearchQuestion) -> ResearchFinding: ...
