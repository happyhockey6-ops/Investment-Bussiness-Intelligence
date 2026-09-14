"""The `PredictionLedger` contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Prediction:
    prediction_id: str
    entity_id: str
    statement: str
    made_at: datetime
    resolves_at: datetime


@dataclass(frozen=True, slots=True)
class Outcome:
    prediction_id: str
    actual_result: str
    resolved_at: datetime
    error_notes: str | None = None


class PredictionLedger(ABC):
    """Not implemented in Phase 0."""

    @abstractmethod
    def record_prediction(self, prediction: Prediction) -> None: ...

    @abstractmethod
    def record_outcome(self, outcome: Outcome) -> None: ...
