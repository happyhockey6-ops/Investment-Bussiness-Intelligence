"""Point-in-time data access and backtest bias guards.

`PointInTimeDataset.as_of` is the single chokepoint through which historical
simulation reads data. Every other engine, when running inside a backtest,
must go through this interface rather than querying `db` directly — that is
what prevents look-ahead bias (using restated/revised figures), survivorship
bias (querying only entities that still exist today), and general
future-information contamination.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


class BacktestBiasError(Exception):
    """Raised when a backtest run detects it was about to use, or did use,
    information not available as of its simulated timestamp."""


@dataclass(frozen=True, slots=True)
class PointInTimeQuery:
    entity_id: str
    as_of: datetime
    """Simulated "now". Only records with retrieval_date <= as_of (see
    `ibi.core.epistemics.Provenance.retrieval_date`) may be returned."""


class PointInTimeDataset(ABC):
    """Read-only, time-bounded view over stored data for backtesting.

    Not implemented in Phase 0 — no concrete backtest data source exists
    yet. This interface is the contract any future implementation
    (e.g. backed by `db.models`) must satisfy.
    """

    @abstractmethod
    def entities_as_of(self, as_of: datetime) -> list[str]:
        """Entity ids that existed / were tracked as of `as_of` — must
        include entities that later failed or were delisted, to avoid
        survivorship bias, and must exclude entities not yet tracked then."""

    @abstractmethod
    def observation_as_of(self, query: PointInTimeQuery) -> object | None:
        """Return the most recent observation for `query.entity_id` with
        `retrieval_date <= query.as_of`, or `None`. Must never return a
        later-arriving restatement of a figure as if it were originally
        reported this way."""
