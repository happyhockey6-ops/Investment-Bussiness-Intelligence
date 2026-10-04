"""Representable absence of an answer.

Data integrity principle (see ARCHITECTURE.md / SECURITY.md): the platform
must never fabricate a value it does not have. Wherever a numeric or
categorical result is expected, the correct type is ``T | Uncertain``
(``Unknown | InsufficientEvidence | ConflictingEvidence | UnverifiedRevision
| IncompatibleBasis``) rather than a guessed value or a silent ``None`` that
looks like "not applicable".

``None`` is intentionally *not* reused for this — ``None`` in this codebase
means "this field does not apply", while the sentinels below mean "this
field applies but the system could not determine it", which downstream code
(and the eventual dashboard) must handle differently.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import TypeVar

T = TypeVar("T")


class UncertaintyReason(StrEnum):
    """Machine-readable reason codes carried by the `Uncertain` states.

    Stored verbatim in `metric_results.reason_code` (Phase 2B), so a value
    here is part of the persisted vocabulary: add new codes freely, but never
    rename or repurpose an existing one.
    """

    # ConflictingEvidence
    TAG_DISAGREEMENT = "tag_disagreement"
    SIMULTANEOUS_DIVERGENCE = "simultaneous_divergence"
    # InsufficientEvidence
    MISSING_INPUT = "missing_input"
    NO_SINGLE_BASIS = "no_single_basis"
    ZERO_DENOMINATOR = "zero_denominator"
    # UnverifiedRevision
    UNCLASSIFIED_8K_DIVERGENCE = "unclassified_8k_divergence"
    UNCLASSIFIED_FORM_DIVERGENCE = "unclassified_form_divergence"
    PARTIAL_PERIODIC_DIVERGENCE = "partial_periodic_divergence"
    NON_RELIANCE_DECLARED = "non_reliance_declared"
    GENERATION_BEHIND_INPUTS = "generation_behind_inputs"
    # IncompatibleBasis
    IDENTITY_VIOLATION = "identity_violation"


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    """One stored fact that contributed to (or contradicted) a result.

    `relation` says how: "input", "corroborating", "conflicting",
    "unverified_revision", "superseded_basis" or "check_term".
    """

    fact_id: int | None
    role: str
    relation: str
    metric_id: str
    accession_number: str
    form_type: str | None
    value: Decimal
    known_available_at: datetime


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
    reason_code: UncertaintyReason | None = None
    evidence: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True, slots=True)
class ConflictingEvidence:
    """Multiple sources disagree and the platform has no basis (yet) to
    prefer one over another."""

    reason: str
    candidate_values: tuple[object, ...] = ()
    reason_code: UncertaintyReason | None = None
    evidence: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True, slots=True)
class UnverifiedRevision:
    """Later evidence diverges from the accepted basis, and the available
    filing evidence cannot classify the divergence (restatement, recast,
    accounting-standard adoption, supplemental disclosure...) — or the
    stored result is known to be behind its inputs. Resolvable by more
    evidence, never by guessing which number is right."""

    reason: str
    reason_code: UncertaintyReason
    evidence: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True, slots=True)
class IncompatibleBasis:
    """Inputs exist, but a deterministic compatibility check on them failed
    (e.g. an accounting identity does not hold within the basis filing)."""

    reason: str
    reason_code: UncertaintyReason
    evidence: tuple[EvidenceRef, ...] = ()
    failed_checks: tuple[str, ...] = ()


Uncertain = (
    Unknown | InsufficientEvidence | ConflictingEvidence | UnverifiedRevision | IncompatibleBasis
)
"""Union of every "we don't know" state. Use as ``T | Uncertain`` on any
field that must never be allowed to silently default to a fabricated value."""


def is_uncertain(value: object) -> bool:
    return isinstance(
        value,
        (Unknown, InsufficientEvidence, ConflictingEvidence, UnverifiedRevision, IncompatibleBasis),
    )
