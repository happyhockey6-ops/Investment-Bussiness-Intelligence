"""Selection policies: which stored facts may become a calculation's inputs.

A `SelectionPolicy` is frozen data, registered under an immutable version
string. Everything that decides *which* facts a calculation uses lives here
— form tiers, the concept→tag map and its priority order, period classes,
identity checks, the 8-K evidence gate — so changing any of it means a new
policy version, never an edit to an existing one. `manifest_hash` makes that
enforceable: each generation stores the hash of the policy it was built
with, and a test pins the hash of every registered version.

Pure: no I/O, no wall clock.

Form tiers (approved Phase 2B policy):
- PERIODIC (10-K, 10-Q and their /A amendments): may supply a calculation's
  single basis, when one accession reports every input for the exact period.
- RECAST (8-K, 8-K/A): may supply a basis only when (a) the accession
  carries every evidence kind listed in `recast_basis_evidence`, and (b)
  every identity check of the metric is evaluable and passes inside it —
  see `resolver._is_eligible_basis`. A complete set of tags is never
  sufficient on its own. Policy v1 lists `xbrl_instance_restatement_classified`,
  evidence that only the filing's own XBRL instance can provide and that
  Phase 2B does not ingest (see DECISIONS.md) — so under v1 no 8-K
  qualifies yet; it corroborates, or, if it diverges, the result is an
  `UnverifiedRevision`.
- OTHER (every other form, and unknown/NULL forms): never a basis;
  divergence fails closed exactly like an 8-K.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import StrEnum


class FormTier(StrEnum):
    PERIODIC = "periodic"
    RECAST = "recast"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class ConceptSpec:
    """One economic concept and the XBRL tags that may report it, in
    priority order. Tags listed together must be genuinely interchangeable:
    within one accession, any disagreement among them is a conflict."""

    name: str
    tags: tuple[str, ...]
    unit: str


@dataclass(frozen=True, slots=True)
class IdentityCheck:
    """`lhs == sum(sign * term)` within the basis accession, for the same
    period, evaluated only when every concept is present there."""

    code: str
    lhs: str
    terms: tuple[tuple[int, str], ...]


@dataclass(frozen=True, slots=True)
class MetricSpec:
    metric_id: str
    unit: str
    period_classes: tuple[str, ...]
    inputs: tuple[tuple[str, str], ...]
    """(role, concept name). In Phase 2B every input shares the metric's
    single duration period (single-period metrics only)."""
    check_concepts: tuple[str, ...] = ()
    identity_checks: tuple[IdentityCheck, ...] = ()


@dataclass(frozen=True, slots=True)
class SelectionPolicy:
    version: str
    periodic_forms: tuple[str, ...]
    recast_forms: tuple[str, ...]
    recast_basis_evidence: tuple[str, ...]
    """Evidence kinds an 8-K must ALL carry to be a basis candidate. Empty
    means 8-Ks can never be a basis."""
    non_reliance_item: str
    period_classes: tuple[tuple[str, int, int], ...]
    """(name, min_days, max_days) — inclusive duration bounds."""
    concepts: tuple[ConceptSpec, ...]
    metrics: tuple[MetricSpec, ...]

    def tier(self, form_type: str | None) -> FormTier:
        if form_type in self.periodic_forms:
            return FormTier.PERIODIC
        if form_type in self.recast_forms:
            return FormTier.RECAST
        return FormTier.OTHER

    def concept(self, name: str) -> ConceptSpec:
        for c in self.concepts:
            if c.name == name:
                return c
        raise KeyError(f"policy {self.version}: unknown concept {name!r}")

    def metric(self, metric_id: str) -> MetricSpec:
        for m in self.metrics:
            if m.metric_id == metric_id:
                return m
        raise KeyError(f"policy {self.version}: unknown metric {metric_id!r}")

    def period_class_of(self, days: int) -> str | None:
        for name, lo, hi in self.period_classes:
            if lo <= days <= hi:
                return name
        return None

    def manifest_hash(self) -> str:
        canonical = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


# Concept→tag map v1. Deliberately short: only tags reviewed as
# interchangeable for the concept. Pending analyst review before any
# production use (see DECISIONS.md, Phase 2B).
_CONCEPTS_V1 = (
    ConceptSpec(
        "revenue",
        (
            "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
            "us-gaap:Revenues",
            "us-gaap:SalesRevenueNet",
        ),
        "USD",
    ),
    ConceptSpec(
        "cost_of_revenue",
        ("us-gaap:CostOfGoodsAndServicesSold", "us-gaap:CostOfRevenue", "us-gaap:CostOfGoodsSold"),
        "USD",
    ),
    ConceptSpec("gross_profit", ("us-gaap:GrossProfit",), "USD"),
    ConceptSpec(
        "operating_cash_flow", ("us-gaap:NetCashProvidedByUsedInOperatingActivities",), "USD"
    ),
    ConceptSpec(
        "capital_expenditures", ("us-gaap:PaymentsToAcquirePropertyPlantAndEquipment",), "USD"
    ),
)

_METRICS_V1 = (
    MetricSpec(
        metric_id="ibi:gross_margin",
        unit="pure",
        period_classes=("quarter", "annual"),
        inputs=(("revenue", "revenue"), ("cost_of_revenue", "cost_of_revenue")),
        check_concepts=("gross_profit",),
        identity_checks=(
            IdentityCheck(
                code="gross_profit_equals_revenue_minus_cost",
                lhs="gross_profit",
                terms=((1, "revenue"), (-1, "cost_of_revenue")),
            ),
        ),
    ),
    MetricSpec(
        metric_id="ibi:free_cash_flow",
        unit="USD",
        # 10-Q cash-flow statements are year-to-date, so only 3-month (Q1)
        # and annual durations ever qualify; YTD periods are excluded.
        period_classes=("quarter", "annual"),
        inputs=(
            ("operating_cash_flow", "operating_cash_flow"),
            ("capital_expenditures", "capital_expenditures"),
        ),
    ),
)

POLICY_V1 = SelectionPolicy(
    version="1",
    periodic_forms=("10-K", "10-K/A", "10-Q", "10-Q/A"),
    recast_forms=("8-K", "8-K/A"),
    recast_basis_evidence=("xbrl_instance_restatement_classified",),
    non_reliance_item="4.02",
    period_classes=(("quarter", 85, 98), ("annual", 350, 378)),
    concepts=_CONCEPTS_V1,
    metrics=_METRICS_V1,
)

POLICIES: dict[str, SelectionPolicy] = {POLICY_V1.version: POLICY_V1}
"""Registered policies. Never edit a registered entry — add a new version."""
