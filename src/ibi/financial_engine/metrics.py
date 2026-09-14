"""A first, real slice of the deterministic metric catalog.

Only a handful of metrics are implemented in Phase 0 — enough to prove the
`MetricCalculation` pattern with real tests (see
tests/unit/test_financial_metrics.py), not the full list in ARCHITECTURE.md.
Every function here is pure and takes `Decimal` for money to avoid float
error in financial arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ibi.core.types import InsufficientEvidence, Uncertain


@dataclass(frozen=True, slots=True)
class RevenueGrowthInputs:
    current_period_revenue: Decimal
    prior_period_revenue: Decimal


def revenue_growth(inputs: RevenueGrowthInputs) -> Decimal | Uncertain:
    """(current - prior) / |prior|.

    Returns `InsufficientEvidence` rather than dividing by zero or returning
    an infinite/undefined growth rate when prior-period revenue is zero.
    """
    if inputs.prior_period_revenue == 0:
        return InsufficientEvidence(
            reason="prior_period_revenue is zero; growth rate is undefined"
        )
    return (
        inputs.current_period_revenue - inputs.prior_period_revenue
    ) / abs(inputs.prior_period_revenue)


@dataclass(frozen=True, slots=True)
class GrossMarginInputs:
    revenue: Decimal
    cost_of_revenue: Decimal


def gross_profit(inputs: GrossMarginInputs) -> Decimal:
    return inputs.revenue - inputs.cost_of_revenue


def gross_margin(inputs: GrossMarginInputs) -> Decimal | Uncertain:
    if inputs.revenue == 0:
        return InsufficientEvidence(reason="revenue is zero; margin is undefined")
    return gross_profit(inputs) / inputs.revenue


@dataclass(frozen=True, slots=True)
class FreeCashFlowInputs:
    operating_cash_flow: Decimal
    capital_expenditures: Decimal
    """Expected as a positive number (spend), consistent with how it is
    reported on the cash flow statement's investing section magnitude."""


def free_cash_flow(inputs: FreeCashFlowInputs) -> Decimal:
    return inputs.operating_cash_flow - inputs.capital_expenditures


@dataclass(frozen=True, slots=True)
class ROICInputs:
    net_operating_profit_after_tax: Decimal
    invested_capital: Decimal


def roic(inputs: ROICInputs) -> Decimal | Uncertain:
    """Return on Invested Capital = NOPAT / Invested Capital.

    `invested_capital` must be strictly positive — a zero or negative
    invested-capital figure means the input data itself needs review, not a
    computed ratio.
    """
    if inputs.invested_capital <= 0:
        return InsufficientEvidence(
            reason=f"invested_capital must be > 0, got {inputs.invested_capital}"
        )
    return inputs.net_operating_profit_after_tax / inputs.invested_capital
