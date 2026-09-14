"""Representable absence of an answer.

Data integrity principle (see ARCHITECTURE.md / SECURITY.md): the platform
must never fabricate a value it does not have. Wherever a numeric or
categorical result is expected, the correct type is
``T | Unknown | InsufficientEvidence | ConflictingEvidence`` rather than a
guessed value or a silent ``None`` that looks like "not applicable".

``None`` is intentionally *not* reused for this — ``None`` in this codebase
means "this field does not apply", while the sentinels below mean "this
field applies but the system could not determine it", which downstream code
(and the eventual dashboard) must handle differently.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Unknown:
    """The value was never looked up, or the platform has no capability to
    determine it yet."""

    reason: str = "not looked up"


@dataclass(frozen=True, slots=True)
class InsufficientEvidence:
    """A lookup was attempted, but the available evidence is too thin to
    support a value at the required confidence."""

    reason: str
    evidence_count: int = 0


@dataclass(frozen=True, slots=True)
class ConflictingEvidence:
    """Multiple sources disagree and the platform has no basis (yet) to
    prefer one over another."""

    reason: str
    candidate_values: tuple[object, ...] = ()


Uncertain = Unknown | InsufficientEvidence | ConflictingEvidence
"""Union of every "we don't know" state. Use as ``T | Uncertain`` on any
field that must never be allowed to silently default to a fabricated value."""


def is_uncertain(value: object) -> bool:
    return isinstance(value, (Unknown, InsufficientEvidence, ConflictingEvidence))
