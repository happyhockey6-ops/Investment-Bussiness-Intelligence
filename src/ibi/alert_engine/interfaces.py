"""The `AlertRule` / `AlertSink` contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Alert:
    entity_id: str
    message: str
    triggered_at: datetime
    severity: str  # e.g. "info" | "warning" | "critical"


class AlertRule(ABC):
    """Not implemented in Phase 0."""

    @abstractmethod
    def evaluate(self, entity_id: str) -> Alert | None: ...


class AlertSink(ABC):
    """Where a triggered alert is delivered (email, Slack, dashboard, ...)."""

    @abstractmethod
    def send(self, alert: Alert) -> None: ...
