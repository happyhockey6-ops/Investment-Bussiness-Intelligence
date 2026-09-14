"""The `MacroIndicatorSource` contract.

A macro indicator (CPI, Fed funds rate, GDP growth, ...) is treated as a
time series of `FACT` or `REPORTED` observations with its own
`ibi.core.epistemics.Provenance` — the same evidentiary discipline as
company-level data, since macro releases are also revised over time.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ibi.core.epistemics import Provenance


@dataclass(frozen=True, slots=True)
class MacroObservation:
    indicator: str
    as_of: date
    value: Decimal
    provenance: Provenance


class MacroIndicatorSource(ABC):
    """Not implemented in Phase 0."""

    @abstractmethod
    def get(self, indicator: str, as_of: date) -> MacroObservation | None: ...
