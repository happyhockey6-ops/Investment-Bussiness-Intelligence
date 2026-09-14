"""financial_engine must be deterministic and must never fabricate a value
for undefined arithmetic (division by zero, etc.) — it must return
InsufficientEvidence instead."""

from __future__ import annotations

from decimal import Decimal

from ibi.core.types import InsufficientEvidence
from ibi.financial_engine.metrics import (
    FreeCashFlowInputs,
    GrossMarginInputs,
    RevenueGrowthInputs,
    ROICInputs,
    free_cash_flow,
    gross_margin,
    gross_profit,
    revenue_growth,
    roic,
)


def test_revenue_growth_positive():
    result = revenue_growth(
        RevenueGrowthInputs(
            current_period_revenue=Decimal("120"), prior_period_revenue=Decimal("100")
        )
    )
    assert result == Decimal("0.20")


def test_revenue_growth_with_zero_prior_is_insufficient_evidence():
    result = revenue_growth(
        RevenueGrowthInputs(
            current_period_revenue=Decimal("100"), prior_period_revenue=Decimal("0")
        )
    )
    assert isinstance(result, InsufficientEvidence)


def test_gross_profit_and_margin():
    inputs = GrossMarginInputs(revenue=Decimal("200"), cost_of_revenue=Decimal("150"))
    assert gross_profit(inputs) == Decimal("50")
    assert gross_margin(inputs) == Decimal("0.25")


def test_gross_margin_with_zero_revenue_is_insufficient_evidence():
    result = gross_margin(GrossMarginInputs(revenue=Decimal("0"), cost_of_revenue=Decimal("10")))
    assert isinstance(result, InsufficientEvidence)


def test_free_cash_flow_is_pure_subtraction():
    result = free_cash_flow(
        FreeCashFlowInputs(operating_cash_flow=Decimal("500"), capital_expenditures=Decimal("120"))
    )
    assert result == Decimal("380")


def test_roic_happy_path():
    result = roic(
        ROICInputs(net_operating_profit_after_tax=Decimal("50"), invested_capital=Decimal("250"))
    )
    assert result == Decimal("0.2")


def test_roic_rejects_non_positive_invested_capital():
    result = roic(
        ROICInputs(net_operating_profit_after_tax=Decimal("50"), invested_capital=Decimal("0"))
    )
    assert isinstance(result, InsufficientEvidence)


def test_calculations_are_deterministic_across_repeated_calls():
    inputs = GrossMarginInputs(revenue=Decimal("333"), cost_of_revenue=Decimal("111"))
    assert gross_margin(inputs) == gross_margin(inputs)
