"""Point-in-time data access and backtest bias guards.

`PointInTimeDataset.as_of` is the single chokepoint through which historical
simulation reads data. Every other engine, when running inside a backtest,
must go through this interface rather than querying `db` directly — that is
what prevents look-ahead bias (using restated/revised figures), survivorship
bias (querying only entities that still exist today), and general
future-information contamination.

Filter on a record's *availability* timestamp, not `retrieval_date`. Phase
1's SEC EDGAR ingestion (`ibi.data_engine.sec_edgar`) established the
concrete distinction, after review found the original Phase 0 framing
below too imprecise: `retrieval_date` (`ibi.core.epistemics.Provenance`)
only says when *this system* fetched a record, which is a poor proxy for
"when could this have been known" — a backfilling ingestion run gives many
years of history the same `retrieval_date`, which would make a backtest
wrongly treat old, genuinely-public information as unknowable until
whatever day the ingestion happened to run. Use the record's own
availability field instead (e.g. `financial_data.known_available_at` — see
DECISIONS.md, "Phase 1: SEC EDGAR ingestion," for the exact derivation
rule and the official SEC documentation it's grounded in).
`retrieval_date` remains useful as a separate, stricter ceiling for a
"what could our own system have said" replay mode, but is not the primary
filter.
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
    """Simulated "now". Only records whose own availability timestamp is
    <= as_of may be returned — see the module docstring for why this is
    not `retrieval_date`."""


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
        """Return the most recent observation for `query.entity_id` whose
        availability timestamp is <= `query.as_of`, or `None`. Must never
        return a later-arriving restatement of a figure as if it were
        originally reported this way."""
