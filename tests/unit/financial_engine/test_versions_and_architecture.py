"""Version pins and architectural guards for Phase 2B.

The pins are the reproducibility contract: a registered policy or formula
version must never change behaviour. If one of these fails, do not update
the pin — register a new version instead (see DECISIONS.md, Phase 2B).
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest

from ibi.financial_engine.formulas import FORMULAS, ResultOutOfRangeError, evaluate, quantize_result
from ibi.financial_engine.policy import POLICIES, POLICY_V1, FormTier
from ibi.financial_engine.results_store import RECAST_EVIDENCE

SRC = Path(__file__).resolve().parents[3] / "src" / "ibi"

PINNED_POLICY_MANIFESTS = {
    "1": "04d8019c0d78454f00c01616175326320ab5099b279336a700973613784feed8",
}


def test_registered_policy_manifests_are_pinned():
    assert set(POLICIES) == set(PINNED_POLICY_MANIFESTS)
    for version, policy in POLICIES.items():
        assert policy.version == version
        assert policy.manifest_hash() == PINNED_POLICY_MANIFESTS[version]


@pytest.mark.parametrize(
    ("key", "inputs", "expected"),
    [
        (("ibi:gross_margin", "1"), {"revenue": "1000", "cost_of_revenue": "600"}, "0.4"),
        (("ibi:gross_margin", "1"), {"revenue": "3", "cost_of_revenue": "1"}, "0.6666666667"),
        (
            ("ibi:free_cash_flow", "1"),
            {"operating_cash_flow": "500", "capital_expenditures": "120"},
            "380",
        ),
    ],
)
def test_formula_golden_outputs(key, inputs, expected):
    result = evaluate(FORMULAS[key], {k: Decimal(v) for k, v in inputs.items()})
    assert result == Decimal(expected)
    assert result.as_tuple().exponent == -10


def test_every_registered_metric_has_a_formula_and_known_concepts():
    for policy in POLICIES.values():
        for spec in policy.metrics:
            assert any(k[0] == spec.metric_id for k in FORMULAS)
            for _, concept in spec.inputs:
                policy.concept(concept)
            for concept in spec.check_concepts:
                policy.concept(concept)


def test_no_tag_belongs_to_two_concepts():
    for policy in POLICIES.values():
        tags = [t for c in policy.concepts for t in c.tags]
        assert len(tags) == len(set(tags))


def test_form_tiers():
    assert POLICY_V1.tier("10-K/A") is FormTier.PERIODIC
    assert POLICY_V1.tier("8-K") is FormTier.RECAST
    assert POLICY_V1.tier("S-4") is FormTier.OTHER
    assert POLICY_V1.tier(None) is FormTier.OTHER
    # v1 requires XBRL-instance classification evidence for an 8-K basis, and
    # Phase 2B has no source for it: the store passes no evidence at all.
    assert POLICY_V1.recast_basis_evidence == ("xbrl_instance_restatement_classified",)
    assert RECAST_EVIDENCE == {}


def test_half_even_quantization_and_range():
    assert quantize_result(Decimal("0.00000000005")) == Decimal("0E-10")
    assert quantize_result(Decimal("0.00000000015")) == Decimal("2E-10")
    with pytest.raises(ResultOutOfRangeError):
        quantize_result(Decimal("1E18"))


PURE_MODULES = ["policy.py", "formulas.py", "resolver.py", "metrics.py"]
FORBIDDEN_IN_PURE = re.compile(
    r"sqlalchemy|ibi\.db|datetime\.now|date\.today|time\.time|import random|urllib|ibi\.config"
)


@pytest.mark.parametrize("name", PURE_MODULES)
def test_pure_financial_modules_have_no_io_or_clock(name):
    source = (SRC / "financial_engine" / name).read_text(encoding="utf-8")
    assert not FORBIDDEN_IN_PURE.search(source), name


RESULT_MODELS = re.compile(
    r"\b(MetricResultRecord|MetricGenerationRecord|MetricResultInputRecord|"
    r"MetricVersionActivationRecord|FactQuarantineRecord)\b"
)
ALLOWED_RESULT_MODEL_USERS = {
    SRC / "financial_engine" / "results_store.py",
    SRC / "db" / "models" / "metric_result.py",
    SRC / "db" / "models" / "__init__.py",
}


def test_only_results_store_touches_result_tables():
    offenders = [
        p for p in SRC.rglob("*.py")
        if p not in ALLOWED_RESULT_MODEL_USERS
        and RESULT_MODELS.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == []
