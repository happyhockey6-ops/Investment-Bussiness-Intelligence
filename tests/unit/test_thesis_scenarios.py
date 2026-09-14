"""A Thesis must always contain exactly one bear, one base, and one bull
scenario — enforced structurally, not by convention."""

from __future__ import annotations

import pytest

from ibi.thesis_engine.interfaces import Scenario, ScenarioKind, Thesis


def _scenario(kind: ScenarioKind) -> Scenario:
    return Scenario(
        kind=kind,
        assumptions=(),
        catalysts=(),
        risks=(),
        unknowns=(),
        invalidation_conditions=(),
        confidence=0.5,
    )


def test_valid_thesis_accepts_one_of_each_scenario():
    thesis = Thesis(
        entity_id="e1",
        bear=_scenario(ScenarioKind.BEAR),
        base=_scenario(ScenarioKind.BASE),
        bull=_scenario(ScenarioKind.BULL),
    )
    assert thesis.entity_id == "e1"


def test_thesis_rejects_duplicate_scenario_kinds():
    with pytest.raises(ValueError, match="exactly one bear, one base, and one bull"):
        Thesis(
            entity_id="e1",
            bear=_scenario(ScenarioKind.BEAR),
            base=_scenario(ScenarioKind.BEAR),
            bull=_scenario(ScenarioKind.BULL),
        )


def test_scenario_confidence_out_of_range_is_rejected():
    with pytest.raises(ValueError, match="confidence"):
        Scenario(
            kind=ScenarioKind.BASE,
            assumptions=(),
            catalysts=(),
            risks=(),
            unknowns=(),
            invalidation_conditions=(),
            confidence=1.5,
        )
