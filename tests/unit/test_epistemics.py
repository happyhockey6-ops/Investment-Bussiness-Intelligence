"""The epistemic model is the platform's core safety property: AI-generated
or unverified content must never be labeled FACT."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ibi.core.epistemics import EpistemicLabel, Evidence, Provenance, SourceTier


def _provenance(tier: SourceTier) -> Provenance:
    return Provenance(
        source="test-source",
        source_tier=tier,
        retrieval_date=datetime.now(UTC),
    )


def test_fact_from_primary_source_is_allowed():
    evidence = Evidence(
        entity_id="e1",
        claim="Revenue was $1B",
        epistemic_label=EpistemicLabel.FACT,
        provenance=_provenance(SourceTier.PRIMARY_REGULATORY),
    )
    assert evidence.epistemic_label == EpistemicLabel.FACT


@pytest.mark.parametrize("tier", [SourceTier.AI_GENERATED, SourceTier.UNVERIFIED])
def test_fact_from_untrustworthy_source_is_rejected(tier: SourceTier):
    with pytest.raises(ValueError, match="cannot be labeled FACT"):
        Evidence(
            entity_id="e1",
            claim="Revenue was $1B",
            epistemic_label=EpistemicLabel.FACT,
            provenance=_provenance(tier),
        )


def test_ai_generated_source_can_still_be_speculation():
    evidence = Evidence(
        entity_id="e1",
        claim="Revenue might grow 10% next year",
        epistemic_label=EpistemicLabel.SPECULATION,
        provenance=_provenance(SourceTier.AI_GENERATED),
    )
    assert evidence.epistemic_label == EpistemicLabel.SPECULATION


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_confidence_out_of_range_is_rejected(confidence: float):
    with pytest.raises(ValueError, match="confidence"):
        Evidence(
            entity_id="e1",
            claim="x",
            epistemic_label=EpistemicLabel.INFERENCE,
            provenance=_provenance(SourceTier.SECONDARY_ANALYSIS),
            confidence=confidence,
        )
