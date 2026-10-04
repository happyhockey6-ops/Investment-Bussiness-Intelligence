"""Versioned formula registry: (metric_id, calculation_version) -> formula.

A registered formula is frozen: a definitional change or bug fix is a new
`calculation_version` entry, and the old entry stays so historical results
remain recomputable. Each formula runs under an explicit local `Decimal`
context, so the caller's global context (precision, rounding) can never
change a stored result. Results are quantized here, once, half-even to 10
decimal places — the scale of `metric_results.value` (NUMERIC(28,10)).

Pure: no I/O, no wall clock.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext

from ibi.core.types import InsufficientEvidence, UncertaintyReason
from ibi.financial_engine.metrics import (
    FreeCashFlowInputs,
    GrossMarginInputs,
    free_cash_flow,
    gross_margin,
)

RESULT_SCALE = Decimal("1E-10")
MAX_INTEGER_DIGITS = 18  # NUMERIC(28,10)
_CONTEXT = Context(prec=50, rounding=ROUND_HALF_EVEN)


class ResultOutOfRangeError(ValueError):
    """A computed value cannot be stored in NUMERIC(28,10) without loss."""


@dataclass(frozen=True, slots=True)
class FormulaSpec:
    metric_id: str
    calculation_version: str
    compute: Callable[[Mapping[str, Decimal]], Decimal | InsufficientEvidence]
    undefined_reason: UncertaintyReason
    """Reason code attached when `compute` returns InsufficientEvidence."""


def quantize_result(value: Decimal) -> Decimal:
    with localcontext(_CONTEXT):
        q = value.quantize(RESULT_SCALE, rounding=ROUND_HALF_EVEN)
    if q.adjusted() >= MAX_INTEGER_DIGITS:
        raise ResultOutOfRangeError(f"{value} exceeds NUMERIC(28,10)")
    return q


def evaluate(spec: FormulaSpec, inputs: Mapping[str, Decimal]) -> Decimal | InsufficientEvidence:
    with localcontext(_CONTEXT):
        raw = spec.compute(inputs)
    if isinstance(raw, InsufficientEvidence):
        return InsufficientEvidence(reason=raw.reason, reason_code=spec.undefined_reason)
    return quantize_result(raw)


def _gross_margin_v1(x: Mapping[str, Decimal]) -> Decimal | InsufficientEvidence:
    return gross_margin(  # only ever Decimal | InsufficientEvidence
        GrossMarginInputs(revenue=x["revenue"], cost_of_revenue=x["cost_of_revenue"])
    )


def _free_cash_flow_v1(x: Mapping[str, Decimal]) -> Decimal | InsufficientEvidence:
    return free_cash_flow(
        FreeCashFlowInputs(
            operating_cash_flow=x["operating_cash_flow"],
            capital_expenditures=x["capital_expenditures"],
        )
    )


FORMULAS: dict[tuple[str, str], FormulaSpec] = {
    ("ibi:gross_margin", "1"): FormulaSpec(
        "ibi:gross_margin", "1", _gross_margin_v1, UncertaintyReason.ZERO_DENOMINATOR
    ),
    ("ibi:free_cash_flow", "1"): FormulaSpec(
        "ibi:free_cash_flow", "1", _free_cash_flow_v1, UncertaintyReason.ZERO_DENOMINATOR
    ),
}
"""Registered formulas. Never edit a registered entry — add a new version."""
