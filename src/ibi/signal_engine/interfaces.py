"""The `SignalGenerator` contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from ibi.core.epistemics import EpistemicLabel


@dataclass(frozen=True, slots=True)
class Signal:
    entity_id: str
    signal_type: str
    strength: float  # 0.0-1.0
    generated_at: datetime
    epistemic_label: EpistemicLabel
    """A signal derived purely from `financial_engine`/`market_engine`
    output is CALCULATION; one that incorporates AI synthesis is INFERENCE
    at best — never FACT."""


class SignalGenerator(ABC):
    """Not implemented in Phase 0."""

    @abstractmethod
    def generate(self, entity_id: str) -> list[Signal]: ...
