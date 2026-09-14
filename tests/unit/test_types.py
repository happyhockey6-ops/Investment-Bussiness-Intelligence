"""The Unknown/InsufficientEvidence/ConflictingEvidence sentinels must be
distinguishable from each other and from a plain value."""

from __future__ import annotations

from ibi.core.types import ConflictingEvidence, InsufficientEvidence, Unknown, is_uncertain


def test_is_uncertain_true_for_all_sentinels():
    assert is_uncertain(Unknown())
    assert is_uncertain(InsufficientEvidence(reason="thin data"))
    assert is_uncertain(ConflictingEvidence(reason="sources disagree"))


def test_is_uncertain_false_for_real_values():
    assert not is_uncertain(42)
    assert not is_uncertain(None)
    assert not is_uncertain("a string")
