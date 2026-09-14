"""Contracts the Financial Engine's future metric catalog must satisfy.

This module defines *shape*, not the catalog itself. The full set of
metrics named in ARCHITECTURE.md (revenue growth, margins, ROIC/ROE/ROA,
FCF, dilution, cash conversion, ...) is Phase 1+ work; Phase 0 establishes
that every metric:

1. Declares its required inputs as a typed dataclass (so missing data is a
   type error, not a `KeyError` at runtime).
2. Returns either the computed value or an `ibi.core.types.Uncertain`
   (never a fabricated number) when a required input is missing.
3. Is independently validatable — see `docs/testing_strategy.md` — before
   any downstream engine (scoring, decision) is allowed to depend on it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

from ibi.core.types import Uncertain

InputsT = TypeVar("InputsT")
ResultT = TypeVar("ResultT")


class MetricCalculation(ABC, Generic[InputsT, ResultT]):
    """A single, named, deterministic financial calculation.

    Implemented as a class (rather than a bare function) so each metric can
    carry a stable `metric_id` for provenance/versioning: when the formula
    for a metric changes, `metric_id` changing (or gaining a version suffix)
    is what lets stored historical calculations remain traceable to the
    formula that produced them.
    """

    metric_id: str

    @abstractmethod
    def compute(self, inputs: InputsT) -> ResultT | Uncertain:
        """Pure function: no I/O, no AI calls, no non-deterministic state."""
