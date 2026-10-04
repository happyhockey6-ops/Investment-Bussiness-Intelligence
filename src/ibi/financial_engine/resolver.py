"""Single-basis input resolution and point-in-time epoch enumeration.

Pure: no I/O, no wall clock, no dependence on input order. Given the stored
facts relevant to one metric for one entity, `compute_history` returns the
metric's full point-in-time history: for every period, one `EpochResult` per
moment its answer changed. `results_store` persists exactly that; the
`live_network` validation test runs exactly the same code in memory.

Resolution rule for one period P as of time T (approved Phase 2B policy):

1. Consider every fact for the metric's concepts with matching unit, exact
   period P and `known_available_at <= T`, from ANY form.
2. Within each accession, resolve each concept across its tags; tags that
   disagree are a conflict, never a choice.
3. The *basis* is the latest eligible accession reporting every input
   (single-basis rule — inputs are never mixed across filings). Eligible:
   any PERIODIC accession; an 8-K/8-K/A only with every evidence kind the
   policy requires (`recast_evidence`) AND every identity check of the
   metric evaluable and passing inside it — a complete set of tags alone is
   never enough. Other/unknown forms are never eligible. Policy v1 requires
   evidence no Phase 2B source produces, so in practice no 8-K qualifies.
4. Any other accession at the basis's timestamp, or any later accession
   (any form), that reports a different value for an input makes the
   result uncertain — the divergence is never resolved by picking a number.
5. Identity checks run inside the basis; an 8-K 4.02 (non-reliance) filed
   after the basis covers every period ending before it.

Precedence: InsufficientEvidence (missing input / no basis) →
ConflictingEvidence → IncompatibleBasis → UnverifiedRevision → formula.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from ibi.core.types import (
    ConflictingEvidence,
    EvidenceRef,
    IncompatibleBasis,
    InsufficientEvidence,
    UncertaintyReason,
    UnverifiedRevision,
)
from ibi.financial_engine.formulas import FormulaSpec, evaluate
from ibi.financial_engine.policy import FormTier, MetricSpec, SelectionPolicy

Period = tuple[date, date]

STATUS_VALUE = "value"
STATUS_BY_TYPE = {
    InsufficientEvidence: "insufficient_evidence",
    ConflictingEvidence: "conflicting_evidence",
    UnverifiedRevision: "unverified_revision",
    IncompatibleBasis: "incompatible_basis",
}


@dataclass(frozen=True, slots=True)
class FactView:
    """The fields of a stored `financial_data` row the resolver may use."""

    fact_id: int | None
    metric_id: str
    unit: str
    start_date: date | None
    end_date: date
    value: Decimal
    accession_number: str
    form_type: str | None
    known_available_at: datetime
    availability_precision: str


@dataclass(frozen=True, slots=True)
class FilingEvent:
    """A filing-level event (Phase 2B: 8-K item codes) from `filings`."""

    accession_number: str
    form_type: str
    items: str | None
    filing_date: date
    known_available_at: datetime
    availability_precision: str


@dataclass(frozen=True, slots=True)
class Resolution:
    status: str
    reason_code: str | None
    reason: str
    value: Decimal | None
    basis_accession: str | None
    revision_kind: str | None
    evidence: tuple[EvidenceRef, ...]
    check_outcomes: tuple[tuple[str, str], ...]

    @property
    def result_hash(self) -> str:
        payload = {
            "status": self.status,
            "reason_code": self.reason_code,
            "value": canonical_decimal(self.value) if self.value is not None else None,
            "basis_accession": self.basis_accession,
            "revision_kind": self.revision_kind,
            "checks": sorted(self.check_outcomes),
            "evidence": sorted(_evidence_key(e) for e in self.evidence),
        }
        return _sha256(payload)


@dataclass(frozen=True, slots=True)
class EpochResult:
    start_date: date
    end_date: date
    effective_from: datetime
    availability_precision: str
    resolution: Resolution


def canonical_decimal(value: Decimal) -> str:
    """Scale-independent text form: Decimal("100.000000") and Decimal("100")
    hash identically (SQLite/PostgreSQL return stored scales; parsing does not)."""
    normalized = value.normalize()
    return format(normalized if normalized != 0 else Decimal(0), "f")


def _sha256(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _evidence_key(e: EvidenceRef) -> list[str]:
    return [
        e.role,
        e.relation,
        e.metric_id,
        e.accession_number,
        e.form_type or "",
        canonical_decimal(e.value),
        e.known_available_at.isoformat(),
    ]


def _fact_key(f: FactView) -> list[str]:
    return [
        f.metric_id,
        f.unit,
        f.start_date.isoformat() if f.start_date else "",
        f.end_date.isoformat(),
        f.accession_number,
        f.form_type or "",
        canonical_decimal(f.value),
        f.known_available_at.isoformat(),
        f.availability_precision,
    ]


# ---------------------------------------------------------------------------
# Accession-level view of one period
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Concept:
    fact: FactView | None  # the priority-tag fact when tags agree
    disagreeing: tuple[FactView, ...]  # non-empty iff tags disagree

    @property
    def value(self) -> Decimal:
        assert self.fact is not None
        return self.fact.value


@dataclass(slots=True)
class _Accession:
    accession_number: str
    known_available_at: datetime
    form_type: str | None
    concepts: dict[str, _Concept]

    @property
    def key(self) -> tuple[datetime, str]:
        return (self.known_available_at, self.accession_number)


def _tag_index(spec: MetricSpec, policy: SelectionPolicy) -> dict[str, tuple[str, int, str]]:
    """tag -> (concept name, priority, unit) for every concept the metric uses."""
    index: dict[str, tuple[str, int, str]] = {}
    for name in [c for _, c in spec.inputs] + list(spec.check_concepts):
        concept = policy.concept(name)
        for priority, tag in enumerate(concept.tags):
            index[tag] = (name, priority, concept.unit)
    return index


def relevant_facts(
    spec: MetricSpec, policy: SelectionPolicy, facts: Iterable[FactView]
) -> list[FactView]:
    """Facts that can influence this metric under this policy: a mapped tag,
    the concept's unit, and a duration in one of the metric's period classes."""
    index = _tag_index(spec, policy)
    out = []
    for f in facts:
        hit = index.get(f.metric_id)
        if hit is None or hit[2] != f.unit or f.start_date is None:
            continue
        if policy.period_class_of((f.end_date - f.start_date).days) in spec.period_classes:
            out.append(f)
    return out


def _accessions(
    spec: MetricSpec, policy: SelectionPolicy, facts: Sequence[FactView]
) -> list[_Accession]:
    index = _tag_index(spec, policy)
    by_accession: dict[str, list[FactView]] = {}
    for f in facts:
        by_accession.setdefault(f.accession_number, []).append(f)

    out = []
    for accn, rows in by_accession.items():
        forms = {r.form_type for r in rows}
        concepts: dict[str, _Concept] = {}
        by_concept: dict[str, list[FactView]] = {}
        for r in rows:
            by_concept.setdefault(index[r.metric_id][0], []).append(r)
        for name, crow in by_concept.items():
            if len({r.value for r in crow}) > 1:
                ordered = tuple(sorted(crow, key=_fact_key))
                concepts[name] = _Concept(fact=None, disagreeing=ordered)
            else:
                best = min(crow, key=lambda r: (index[r.metric_id][1], _fact_key(r)))
                concepts[name] = _Concept(fact=best, disagreeing=())
        out.append(
            _Accession(
                accession_number=accn,
                # All facts of one accession share an availability; max() is
                # the conservative choice should SEC metadata ever disagree.
                known_available_at=max(r.known_available_at for r in rows),
                # Mixed forms within one accession cannot be classified.
                form_type=forms.pop() if len(forms) == 1 else None,
                concepts=concepts,
            )
        )
    return sorted(out, key=lambda a: a.key)


def _ref(f: FactView, role: str, relation: str) -> EvidenceRef:
    return EvidenceRef(
        fact_id=f.fact_id,
        role=role,
        relation=relation,
        metric_id=f.metric_id,
        accession_number=f.accession_number,
        form_type=f.form_type,
        value=f.value,
        known_available_at=f.known_available_at,
    )


def _concept_refs(c: _Concept, role: str, relation: str) -> list[EvidenceRef]:
    if c.fact is not None:
        return [_ref(c.fact, role, relation)]
    return [_ref(f, role, relation) for f in c.disagreeing]


def _uncertain(
    kind: type, code: UncertaintyReason, reason: str, evidence: list[EvidenceRef],
    checks: dict[str, str], basis: str | None = None,
) -> Resolution:
    return Resolution(
        status=STATUS_BY_TYPE[kind],
        reason_code=code.value,
        reason=reason,
        value=None,
        basis_accession=basis,
        revision_kind=None,
        evidence=_dedupe(evidence),
        check_outcomes=tuple(sorted(checks.items())),
    )


def _dedupe(evidence: list[EvidenceRef]) -> tuple[EvidenceRef, ...]:
    seen: dict[tuple, EvidenceRef] = {}
    for e in evidence:
        seen.setdefault(tuple(_evidence_key(e)), e)
    return tuple(seen[k] for k in sorted(seen))


_DIVERGENCE_CODE = {
    FormTier.RECAST: UncertaintyReason.UNCLASSIFIED_8K_DIVERGENCE,
    FormTier.OTHER: UncertaintyReason.UNCLASSIFIED_FORM_DIVERGENCE,
    FormTier.PERIODIC: UncertaintyReason.PARTIAL_PERIODIC_DIVERGENCE,
}
_DIVERGENCE_ORDER = (FormTier.RECAST, FormTier.OTHER, FormTier.PERIODIC)

RecastEvidence = Mapping[str, frozenset[str]]
"""accession_number -> evidence kinds positively classifying that 8-K as a
complete, consistent restatement/recast. No Phase 2B source produces any
(see `policy.SelectionPolicy.recast_basis_evidence`); it is an explicit
input so the eligibility path is exercised and hashed, never inferred."""


def _identity_outcomes(spec: MetricSpec, acc: _Accession) -> dict[str, str]:
    """passed / failed / not_applicable for each identity check, in `acc`."""
    outcomes: dict[str, str] = {}
    for check in spec.identity_checks:
        names = [check.lhs] + [name for _, name in check.terms]
        present = [acc.concepts.get(n) for n in names]
        if any(p is None for p in present):
            outcomes[check.code] = "not_applicable"
        elif any(p.disagreeing for p in present if p is not None):
            outcomes[check.code] = "failed"
        else:
            lhs = acc.concepts[check.lhs].value
            rhs = sum((sign * acc.concepts[n].value for sign, n in check.terms), Decimal(0))
            outcomes[check.code] = "passed" if lhs == rhs else "failed"
    return outcomes


def _is_eligible_basis(
    spec: MetricSpec, policy: SelectionPolicy, acc: _Accession, recast_evidence: RecastEvidence
) -> bool:
    tier = policy.tier(acc.form_type)
    if tier is FormTier.PERIODIC:
        return True
    if tier is not FormTier.RECAST or not policy.recast_basis_evidence:
        return False
    # An 8-K needs (a) every evidence kind the policy requires, and (b) a
    # demonstrably consistent basis: every identity check applicable to the
    # metric must be evaluable inside the 8-K and pass. Completeness of tags
    # alone is never sufficient.
    kinds = recast_evidence.get(acc.accession_number, frozenset())
    if not set(policy.recast_basis_evidence) <= kinds:
        return False
    return all(o == "passed" for o in _identity_outcomes(spec, acc).values())


def resolve(
    spec: MetricSpec,
    policy: SelectionPolicy,
    formula: FormulaSpec,
    period: Period,
    facts: Sequence[FactView],
    events: Sequence[FilingEvent],
    as_of: datetime,
    non_reliance_coverage: str = "unknown",
    recast_evidence: RecastEvidence | None = None,
) -> Resolution:
    """Resolve one period as of `as_of`. `facts` may contain anything; only
    relevant, period-matching, already-available facts are considered."""
    recast_evidence = recast_evidence or {}
    start, end = period
    pool = [
        f for f in relevant_facts(spec, policy, facts)
        if f.start_date == start and f.end_date == end and f.known_available_at <= as_of
    ]
    accessions = _accessions(spec, policy, pool)
    input_concepts = [(role, concept) for role, concept in spec.inputs]
    checks: dict[str, str] = {"non_reliance_scan": non_reliance_coverage}

    def latest_refs(relation: str) -> list[EvidenceRef]:
        refs: list[EvidenceRef] = []
        for role, concept in input_concepts:
            having = [a for a in accessions if concept in a.concepts]
            if having:
                refs += _concept_refs(having[-1].concepts[concept], role, relation)
        return refs

    # 1. Missing inputs / no single basis.
    missing = [role for role, c in input_concepts if not any(c in a.concepts for a in accessions)]
    if missing:
        return _uncertain(
            InsufficientEvidence, UncertaintyReason.MISSING_INPUT,
            f"no fact reports input(s) {missing} for {start}..{end}",
            latest_refs("input"), checks,
        )
    full = [
        a for a in accessions
        if all(c in a.concepts for _, c in input_concepts)
        and _is_eligible_basis(spec, policy, a, recast_evidence)
    ]
    if not full:
        return _uncertain(
            InsufficientEvidence, UncertaintyReason.NO_SINGLE_BASIS,
            "no single eligible filing reports every input; inputs are never "
            "combined across filings",
            latest_refs("input"), checks,
        )
    basis = full[-1]
    basis_refs = [
        ref for role, c in input_concepts for ref in _concept_refs(basis.concepts[c], role, "input")
    ]

    # 2. Conflicts: tag disagreement in the basis, same-time divergence,
    #    tag disagreement in any later accession.
    disagreeing = [
        (role, c) for role, c in input_concepts if basis.concepts[c].disagreeing
    ]
    if disagreeing:
        refs = [
            ref for role, c in disagreeing
            for ref in _concept_refs(basis.concepts[c], role, "conflicting")
        ]
        return _uncertain(
            ConflictingEvidence, UncertaintyReason.TAG_DISAGREEMENT,
            f"interchangeable tags disagree within {basis.accession_number}",
            refs, checks, basis.accession_number,
        )

    corroborating: list[EvidenceRef] = []
    simultaneous: list[EvidenceRef] = []
    later_conflicts: list[EvidenceRef] = []
    divergences: dict[FormTier, list[EvidenceRef]] = {}
    for other in accessions:
        if other is basis or other.known_available_at < basis.known_available_at:
            continue
        same_time = other.known_available_at == basis.known_available_at
        for role, c in input_concepts:
            oc = other.concepts.get(c)
            if oc is None:
                continue
            if oc.disagreeing:
                target = simultaneous if same_time else later_conflicts
                target += _concept_refs(oc, role, "conflicting")
            elif oc.value != basis.concepts[c].value:
                if same_time:
                    simultaneous += _concept_refs(oc, role, "conflicting")
                else:
                    tier = policy.tier(other.form_type)
                    divergences.setdefault(tier, []).extend(
                        _concept_refs(oc, role, "unverified_revision")
                    )
            else:
                corroborating += _concept_refs(oc, role, "corroborating")

    if simultaneous:
        basis_conflicting = [
            ref for role, c in input_concepts
            for ref in _concept_refs(basis.concepts[c], role, "conflicting")
        ]
        return _uncertain(
            ConflictingEvidence, UncertaintyReason.SIMULTANEOUS_DIVERGENCE,
            "filings with the same availability timestamp report different values",
            basis_conflicting + simultaneous, checks, basis.accession_number,
        )
    if later_conflicts:
        return _uncertain(
            ConflictingEvidence, UncertaintyReason.TAG_DISAGREEMENT,
            "interchangeable tags disagree within a later filing",
            basis_refs + later_conflicts, checks, basis.accession_number,
        )

    # 3. Identity checks inside the basis.
    outcomes = _identity_outcomes(spec, basis)
    checks.update(outcomes)
    failed = [code for code, outcome in outcomes.items() if outcome == "failed"]
    check_refs: list[EvidenceRef] = []
    for check in spec.identity_checks:
        if outcomes[check.code] == "not_applicable":
            continue
        for n in [check.lhs] + [name for _, name in check.terms]:
            check_refs += _concept_refs(basis.concepts[n], n, "check_term")
    if failed:
        return Resolution(
            status=STATUS_BY_TYPE[IncompatibleBasis],
            reason_code=UncertaintyReason.IDENTITY_VIOLATION.value,
            reason=f"identity check(s) failed within {basis.accession_number}: {failed}",
            value=None,
            basis_accession=basis.accession_number,
            revision_kind=None,
            evidence=_dedupe(basis_refs + check_refs),
            check_outcomes=tuple(sorted(checks.items())),
        )

    # 4. Unverified revisions: later divergent evidence, then non-reliance.
    for tier in _DIVERGENCE_ORDER:
        if tier in divergences:
            return _uncertain(
                UnverifiedRevision, _DIVERGENCE_CODE[tier],
                f"later {tier.value}-tier evidence diverges from basis "
                f"{basis.accession_number} and cannot be classified",
                basis_refs + [r for refs in divergences.values() for r in refs] + corroborating,
                checks, basis.accession_number,
            )
    non_reliance = [
        e for e in events
        if policy.tier(e.form_type) is FormTier.RECAST
        and e.items is not None
        and policy.non_reliance_item in [i.strip() for i in e.items.split(",")]
        and basis.known_available_at < e.known_available_at <= as_of
        and end < e.filing_date
    ]
    if non_reliance:
        accns = sorted(e.accession_number for e in non_reliance)
        return _uncertain(
            UnverifiedRevision, UncertaintyReason.NON_RELIANCE_DECLARED,
            f"item {policy.non_reliance_item} non-reliance filed after the basis: {accns}",
            basis_refs + corroborating, checks, basis.accession_number,
        )

    # 5. Revision annotation (value still accepted: single eligible basis).
    revision_kind = None
    superseded: list[EvidenceRef] = []
    earlier = [
        a for a in full[:-1]
        if all(not a.concepts[c].disagreeing for _, c in input_concepts)
    ]
    if earlier:
        prev = earlier[-1]
        if any(prev.concepts[c].value != basis.concepts[c].value for _, c in input_concepts):
            if policy.tier(basis.form_type) is FormTier.RECAST:
                revision_kind = "recast"  # evidence-qualified 8-K (see _is_eligible_basis)
            elif (basis.form_type or "").endswith("/A"):
                revision_kind = "amendment"
            else:
                revision_kind = "comparative_revision_unexplained"
            superseded = [
                ref for role, c in input_concepts
                for ref in _concept_refs(prev.concepts[c], role, "superseded_basis")
            ]

    # 6. Formula.
    outcome = evaluate(formula, {role: basis.concepts[c].value for role, c in input_concepts})
    evidence = _dedupe(basis_refs + check_refs + corroborating + superseded)
    if isinstance(outcome, InsufficientEvidence):
        return Resolution(
            status=STATUS_BY_TYPE[InsufficientEvidence],
            reason_code=(outcome.reason_code or UncertaintyReason.ZERO_DENOMINATOR).value,
            reason=outcome.reason,
            value=None,
            basis_accession=basis.accession_number,
            revision_kind=revision_kind,
            evidence=evidence,
            check_outcomes=tuple(sorted(checks.items())),
        )
    return Resolution(
        status=STATUS_VALUE,
        reason_code=None,
        reason="",
        value=outcome,
        basis_accession=basis.accession_number,
        revision_kind=revision_kind,
        evidence=evidence,
        check_outcomes=tuple(sorted(checks.items())),
    )


# ---------------------------------------------------------------------------
# Epochs and snapshot identity
# ---------------------------------------------------------------------------


def _is_non_reliance(policy: SelectionPolicy, e: FilingEvent) -> bool:
    return (
        policy.tier(e.form_type) is FormTier.RECAST
        and e.items is not None
        and policy.non_reliance_item in [i.strip() for i in e.items.split(",")]
    )


def compute_history(
    spec: MetricSpec,
    policy: SelectionPolicy,
    formula: FormulaSpec,
    facts: Sequence[FactView],
    events: Sequence[FilingEvent],
    non_reliance_coverage: str = "unknown",
    recast_evidence: RecastEvidence | None = None,
) -> list[EpochResult]:
    """Every period's point-in-time history. An epoch is a distinct
    `known_available_at` of a relevant fact (or a non-reliance event); a row
    is emitted only when the resolution's `result_hash` changes, so the
    latest row at or before T is exactly the answer as of T."""
    pool = relevant_facts(spec, policy, facts)
    input_names = {c for _, c in spec.inputs}
    index = _tag_index(spec, policy)
    periods = sorted(
        {
            (f.start_date, f.end_date) for f in pool
            if index[f.metric_id][0] in input_names and f.start_date is not None
        }
    )
    reliance_events = [e for e in events if _is_non_reliance(policy, e)]

    out: list[EpochResult] = []
    for start, end in periods:
        pf = [f for f in pool if f.start_date == start and f.end_date == end]
        first = min(f.known_available_at for f in pf)
        triggers: dict[datetime, set[str]] = {}
        for f in pf:
            triggers.setdefault(f.known_available_at, set()).add(f.availability_precision)
        for e in reliance_events:
            if end < e.filing_date and e.known_available_at >= first:
                triggers.setdefault(e.known_available_at, set()).add(e.availability_precision)

        last_hash = None
        for epoch in sorted(triggers):
            resolution = resolve(
                spec, policy, formula, (start, end), pf, reliance_events, epoch,
                non_reliance_coverage, recast_evidence,
            )
            if resolution.result_hash == last_hash:
                continue
            last_hash = resolution.result_hash
            precisions = triggers[epoch]
            out.append(
                EpochResult(
                    start_date=start,
                    end_date=end,
                    effective_from=epoch,
                    availability_precision=(
                        "day_conservative" if "day_conservative" in precisions
                        else "acceptance_timestamp"
                    ),
                    resolution=resolution,
                )
            )
    return out


def input_set_hash(
    spec: MetricSpec,
    policy: SelectionPolicy,
    facts: Sequence[FactView],
    events: Sequence[FilingEvent],
    non_reliance_coverage: str = "unknown",
    recast_evidence: RecastEvidence | None = None,
) -> str:
    """Content identity of everything that can influence `compute_history`."""
    return _sha256(
        {
            "recast_evidence": sorted(
                [accn, sorted(kinds)] for accn, kinds in (recast_evidence or {}).items()
            ),
            "facts": sorted(_fact_key(f) for f in relevant_facts(spec, policy, facts)),
            "events": sorted(
                [
                    e.accession_number, e.form_type, e.items or "", e.filing_date.isoformat(),
                    e.known_available_at.isoformat(), e.availability_precision,
                ]
                for e in events if _is_non_reliance(policy, e)
            ),
            "non_reliance_coverage": non_reliance_coverage,
        }
    )
