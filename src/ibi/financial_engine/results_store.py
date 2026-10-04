"""Persistence and typed reads for Phase 2B calculation results.

The I/O boundary of `financial_engine` — mirroring `data_engine.sec_edgar`,
where `mapping.py` is pure and `ingest.py` does the I/O. Everything that
decides a number lives in the pure modules (`policy`, `formulas`,
`resolver`); this module only loads an exactly-identified input snapshot,
runs them, verifies, and writes immutable rows.

Build rules (approved Phase 2B decisions):
- A generation is identified by (entity, metric, calculation_version,
  selection_policy_version, input_set_hash). Rebuilding over an unchanged
  snapshot writes nothing (idempotent).
- Before any write, the latest existing generation for the same version pair
  is re-derived from its own watermarks: if its input snapshot no longer
  hashes the same → `InputMutationError`; if recomputation no longer
  reproduces the stored rows it saw → `ResultDriftError`; if the registered
  policy's content no longer matches the hash it was built with →
  `PolicyManifestMismatchError`.
- Results are append-only per version pair. A new build may only add epochs
  after the previous build's last epoch; any change at or before it →
  `RetroactiveDriftError`, nothing is written, and the remedy is an explicit
  new selection-policy version or a separately approved recovery (C3).
  Unchanged history is therefore never copied: each row is written once, by
  the generation that first produced it (C2).

Read rules: every read takes an explicit (calculation_version,
selection_policy_version) pair — there is no default and no fallback to
another pair. A reader picks the single latest row at or before `as_of` and
never filters by status, so an uncertain epoch can never be skipped in
favour of an older value. The current-knowledge reader also refuses (typed
`UnverifiedRevision`) to answer from a generation that is behind inputs
already available as of `as_of`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ibi.core.types import (
    ConflictingEvidence,
    EvidenceRef,
    IncompatibleBasis,
    InsufficientEvidence,
    Uncertain,
    UncertaintyReason,
    Unknown,
    UnverifiedRevision,
    is_uncertain,
)
from ibi.db.locks import acquire_entity_write_lock
from ibi.db.models import (
    FactQuarantineRecord,
    FilingRecord,
    FinancialDataPointRecord,
    MetricGenerationRecord,
    MetricResultInputRecord,
    MetricResultRecord,
    MetricVersionActivationRecord,
)
from ibi.financial_engine.formulas import FORMULAS, FormulaSpec
from ibi.financial_engine.policy import POLICIES, MetricSpec, SelectionPolicy
from ibi.financial_engine.resolver import (
    EpochResult,
    FactView,
    FilingEvent,
    compute_history,
    input_set_hash,
    relevant_facts,
)

NON_RELIANCE_COVERAGE = "submissions_recent_window_only"
"""Ingestion reads only submissions.json's "recent" window, so 8-K item
codes (and therefore 4.02 detection) cover recent filings only."""

RECAST_EVIDENCE: dict[str, frozenset[str]] = {}
"""No Phase 2B source classifies an 8-K as a complete, consistent recast
(that needs the filing's own XBRL instance, not ingested). Kept explicit and
empty so the gap is visible, hashed into every snapshot, and never inferred."""


class ResultStoreError(Exception):
    """Base for integrity failures that stop a build; nothing is written."""


class ResultDriftError(ResultStoreError):
    """Recomputing an existing generation from its own snapshot no longer
    reproduces the rows it saw (code changed without a version bump, or
    non-determinism)."""


class PolicyManifestMismatchError(ResultDriftError):
    """A registered policy version's content changed after it was used."""


class InputMutationError(ResultStoreError):
    """An existing generation's input snapshot can no longer be reproduced —
    stored facts/filings were modified or deleted (append-only violated)."""


class RetroactiveDriftError(ResultStoreError):
    """New inputs would change history at or before the previous
    generation's last epoch. Requires an explicit new selection-policy
    version or a separately approved recovery procedure."""


class InputIntegrityError(ResultStoreError):
    """A relevant stored fact cannot be placed in time (NULL availability)."""


class NoActiveVersionError(LookupError):
    pass


class UncertainResultError(ValueError):
    """Raised by `require_values` when a series contains uncertainty."""


@dataclass(frozen=True, slots=True)
class VersionPair:
    calculation_version: str
    selection_policy_version: str


@dataclass(frozen=True, slots=True)
class BuildOutcome:
    status: str  # "created" | "unchanged"
    generation_id: int
    kind: str
    rows_written: int


@dataclass(frozen=True, slots=True)
class ResolvedMetric:
    value: Decimal
    result_id: int
    generation_id: int
    versions: VersionPair
    effective_from: datetime
    basis_accession: str | None
    revision_kind: str | None
    evidence: tuple[EvidenceRef, ...]


MetricReadResult = ResolvedMetric | Uncertain


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc(dt: datetime) -> datetime:
    """SQLite returns naive datetimes for timestamptz columns; every stored
    timestamp in this schema is UTC."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _require_aware(dt: datetime, name: str) -> datetime:
    if dt.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return dt.astimezone(UTC)


def _resolve_versions(
    metric_id: str, versions: VersionPair
) -> tuple[SelectionPolicy, MetricSpec, FormulaSpec]:
    try:
        policy = POLICIES[versions.selection_policy_version]
        formula = FORMULAS[(metric_id, versions.calculation_version)]
    except KeyError as e:
        raise KeyError(f"unregistered version pair for {metric_id}: {versions}") from e
    return policy, policy.metric(metric_id), formula


def _metric_tags(spec: MetricSpec, policy: SelectionPolicy) -> list[str]:
    names = [c for _, c in spec.inputs] + list(spec.check_concepts)
    return sorted({t for n in names for t in policy.concept(n).tags})


def _series(entity_id: str, metric_id: str, versions: VersionPair) -> tuple:
    return (
        MetricResultRecord.entity_id == entity_id,
        MetricResultRecord.metric_id == metric_id,
        MetricResultRecord.calculation_version == versions.calculation_version,
        MetricResultRecord.selection_policy_version == versions.selection_policy_version,
    )


def _generations(entity_id: str, metric_id: str, versions: VersionPair) -> tuple:
    return (
        MetricGenerationRecord.entity_id == entity_id,
        MetricGenerationRecord.metric_id == metric_id,
        MetricGenerationRecord.calculation_version == versions.calculation_version,
        MetricGenerationRecord.selection_policy_version == versions.selection_policy_version,
    )


def _watermarks(session: Session) -> tuple[int, int, int]:
    def mx(col) -> int:
        return session.execute(select(func.max(col))).scalar() or 0

    return (
        mx(FinancialDataPointRecord.id),
        mx(FilingRecord.id),
        mx(FactQuarantineRecord.id),
    )


def _load_snapshot(
    session: Session,
    entity_id: str,
    spec: MetricSpec,
    policy: SelectionPolicy,
    fact_wm: int,
    filing_wm: int,
    quarantine_wm: int,
) -> tuple[list[FactView], list[FilingEvent]]:
    quarantined = set(
        session.execute(
            select(FactQuarantineRecord.fact_id).where(FactQuarantineRecord.id <= quarantine_wm)
        ).scalars()
    )
    rows = session.execute(
        select(FinancialDataPointRecord).where(
            FinancialDataPointRecord.entity_id == entity_id,
            FinancialDataPointRecord.metric_id.in_(_metric_tags(spec, policy)),
            FinancialDataPointRecord.epistemic_label == "fact",
            FinancialDataPointRecord.id <= fact_wm,
        )
    ).scalars()
    facts: list[FactView] = []
    for r in rows:
        if r.id in quarantined:
            continue
        if r.known_available_at is None or r.availability_precision is None or r.value is None:
            raise InputIntegrityError(
                f"financial_data.id={r.id} has no availability/value; cannot be placed in time"
            )
        facts.append(
            FactView(
                fact_id=r.id,
                metric_id=r.metric_id,
                unit=r.unit,
                start_date=r.start_date,
                end_date=r.period_end_date,
                value=Decimal(r.value),
                accession_number=r.accession_number or "",
                form_type=r.source_form_type,
                known_available_at=_utc(r.known_available_at),
                availability_precision=r.availability_precision,
            )
        )
    filings = session.execute(
        select(FilingRecord).where(
            FilingRecord.entity_id == entity_id,
            FilingRecord.form_type.in_(policy.recast_forms),
            FilingRecord.id <= filing_wm,
        )
    ).scalars()
    events = [
        FilingEvent(
            accession_number=f.accession_number,
            form_type=f.form_type,
            items=f.items,
            filing_date=f.filing_date,
            known_available_at=_utc(f.known_available_at),
            availability_precision=f.availability_precision,
        )
        for f in filings
    ]
    return facts, events


def _row_key(start: date | None, end: date, eff: datetime, h: str) -> tuple:
    return (start, end, _utc(eff), h)


def _history_keys(history: Sequence[EpochResult]) -> set[tuple]:
    return {
        _row_key(r.start_date, r.end_date, r.effective_from, r.resolution.result_hash)
        for r in history
    }


def _stored_keys(
    session: Session, entity_id: str, metric_id: str, versions: VersionPair, up_to_generation: int
) -> set[tuple]:
    rows = session.execute(
        select(MetricResultRecord).where(
            *_series(entity_id, metric_id, versions),
            MetricResultRecord.generation_id <= up_to_generation,
        )
    ).scalars()
    return {
        _row_key(r.start_date, r.period_end_date, r.effective_from, r.result_hash) for r in rows
    }


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def build_generation(
    session: Session,
    entity_id: str,
    metric_id: str,
    versions: VersionPair,
) -> BuildOutcome:
    """Build (or confirm) the generation for the current input snapshot.
    Runs inside the caller's transaction; raises before writing anything on
    any integrity failure."""
    policy, spec, formula = _resolve_versions(metric_id, versions)
    acquire_entity_write_lock(session, entity_id)
    manifest = policy.manifest_hash()

    def snapshot(fwm: int, filwm: int, qwm: int):
        facts, events = _load_snapshot(session, entity_id, spec, policy, fwm, filwm, qwm)
        args = (NON_RELIANCE_COVERAGE, RECAST_EVIDENCE)
        h = input_set_hash(spec, policy, facts, events, *args)
        history = compute_history(spec, policy, formula, facts, events, *args)
        return h, history

    prev = session.execute(
        select(MetricGenerationRecord).where(*_generations(entity_id, metric_id, versions))
        .order_by(MetricGenerationRecord.id.desc()).limit(1)
    ).scalar_one_or_none()

    prev_stored: set[tuple] = set()
    if prev is not None:
        if prev.policy_manifest_hash != manifest:
            raise PolicyManifestMismatchError(
                f"policy {versions.selection_policy_version} content changed since "
                f"generation {prev.id}"
            )
        prev_hash, prev_history = snapshot(
            prev.fact_watermark, prev.filing_watermark, prev.quarantine_watermark
        )
        if prev_hash != prev.input_set_hash:
            raise InputMutationError(f"generation {prev.id}: input snapshot no longer reproducible")
        prev_stored = _stored_keys(session, entity_id, metric_id, versions, prev.id)
        if _history_keys(prev_history) != prev_stored:
            raise ResultDriftError(
                f"generation {prev.id}: recomputation does not reproduce its stored rows"
            )

    fwm, filwm, qwm = _watermarks(session)
    h, history = snapshot(fwm, filwm, qwm)

    existing = session.execute(
        select(MetricGenerationRecord).where(
            *_generations(entity_id, metric_id, versions),
            MetricGenerationRecord.input_set_hash == h,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return BuildOutcome("unchanged", existing.id, existing.kind, 0)

    kind = "initial"
    to_write = list(history)
    if prev is not None:
        kind = "new_vintage"
        if prev.max_effective_from is not None:
            cutoff = _utc(prev.max_effective_from)
            before = {k for k in _history_keys(history) if k[2] <= cutoff}
            if before != prev_stored:
                changed = sorted(k[2] for k in before ^ prev_stored)
                raise RetroactiveDriftError(
                    f"{entity_id} {metric_id}: new inputs change history at or before "
                    f"{cutoff.isoformat()} (first change at {changed[0].isoformat()}); "
                    f"an explicit new selection-policy version or an approved recovery "
                    f"procedure is required"
                )
            to_write = [r for r in history if _utc(r.effective_from) > cutoff]

    generation = MetricGenerationRecord(
        entity_id=entity_id,
        metric_id=metric_id,
        calculation_version=versions.calculation_version,
        selection_policy_version=versions.selection_policy_version,
        policy_manifest_hash=manifest,
        fact_watermark=fwm,
        filing_watermark=filwm,
        quarantine_watermark=qwm,
        input_set_hash=h,
        kind=kind,
        max_effective_from=max((r.effective_from for r in history), default=None),
    )
    session.add(generation)
    session.flush()

    for r in to_write:
        res = r.resolution
        row = MetricResultRecord(
            generation_id=generation.id,
            entity_id=entity_id,
            metric_id=metric_id,
            calculation_version=versions.calculation_version,
            selection_policy_version=versions.selection_policy_version,
            unit=spec.unit,
            start_date=r.start_date,
            period_end_date=r.end_date,
            effective_from=r.effective_from,
            availability_precision=r.availability_precision,
            status=res.status,
            reason_code=res.reason_code,
            value=res.value,
            basis_accession=res.basis_accession,
            revision_kind=res.revision_kind,
            check_outcomes=dict(res.check_outcomes),
            result_hash=res.result_hash,
            epistemic_label="calculation",
        )
        session.add(row)
        session.flush()
        for e in res.evidence:
            if e.fact_id is None:  # pragma: no cover - snapshot facts always have ids
                raise InputIntegrityError("provenance without a stored fact id")
            session.add(
                MetricResultInputRecord(
                    result_id=row.id, fact_id=e.fact_id, role=e.role, relation=e.relation
                )
            )
    session.flush()
    return BuildOutcome("created", generation.id, kind, len(to_write))


# ---------------------------------------------------------------------------
# Version activation and quarantine (append-only)
# ---------------------------------------------------------------------------


def activate_versions(
    session: Session, metric_id: str, versions: VersionPair, note: str | None = None
) -> int:
    _resolve_versions(metric_id, versions)
    record = MetricVersionActivationRecord(
        metric_id=metric_id,
        calculation_version=versions.calculation_version,
        selection_policy_version=versions.selection_policy_version,
        note=note,
    )
    session.add(record)
    session.flush()
    return record.id


def active_versions(
    session: Session, metric_id: str, system_time: datetime | None = None
) -> VersionPair:
    """Look up the pair designated active (now, or at `system_time`). Reads
    never call this implicitly — callers pass the returned pair explicitly."""
    q = select(MetricVersionActivationRecord).where(
        MetricVersionActivationRecord.metric_id == metric_id
    )
    if system_time is not None:
        q = q.where(
            MetricVersionActivationRecord.created_at <= _require_aware(system_time, "system_time")
        )
    record = session.execute(
        q.order_by(MetricVersionActivationRecord.id.desc()).limit(1)
    ).scalar_one_or_none()
    if record is None:
        raise NoActiveVersionError(f"no active versions for {metric_id}")
    return VersionPair(record.calculation_version, record.selection_policy_version)


def quarantine_fact(session: Session, fact_id: int, reason: str) -> int:
    if not reason.strip():
        raise ValueError("a quarantine reason is required")
    if session.get(FinancialDataPointRecord, fact_id) is None:
        raise LookupError(f"financial_data.id={fact_id} does not exist")
    record = FactQuarantineRecord(fact_id=fact_id, reason=reason)
    session.add(record)
    session.flush()
    return record.id


# ---------------------------------------------------------------------------
# Typed reads
# ---------------------------------------------------------------------------


def _inputs_for(session: Session, result_id: int) -> tuple[EvidenceRef, ...]:
    rows = session.execute(
        select(MetricResultInputRecord, FinancialDataPointRecord)
        .join(
            FinancialDataPointRecord,
            FinancialDataPointRecord.id == MetricResultInputRecord.fact_id,
        )
        .where(MetricResultInputRecord.result_id == result_id)
        .order_by(MetricResultInputRecord.id)
    ).all()
    return tuple(
        EvidenceRef(
            fact_id=f.id,
            role=link.role,
            relation=link.relation,
            metric_id=f.metric_id,
            accession_number=f.accession_number or "",
            form_type=f.source_form_type,
            value=Decimal(f.value),
            known_available_at=_utc(f.known_available_at),
        )
        for link, f in rows
    )


def _typed(session: Session, row: MetricResultRecord) -> MetricReadResult:
    evidence = _inputs_for(session, row.id)
    if row.status == "value":
        return ResolvedMetric(
            value=Decimal(row.value),
            result_id=row.id,
            generation_id=row.generation_id,
            versions=VersionPair(row.calculation_version, row.selection_policy_version),
            effective_from=_utc(row.effective_from),
            basis_accession=row.basis_accession,
            revision_kind=row.revision_kind,
            evidence=evidence,
        )
    code = UncertaintyReason(row.reason_code)
    reason = f"{code.value} (metric_results.id={row.id}, generation {row.generation_id})"
    if row.status == "conflicting_evidence":
        return ConflictingEvidence(
            reason=reason,
            candidate_values=tuple(e.value for e in evidence if e.relation == "conflicting"),
            reason_code=code,
            evidence=evidence,
        )
    if row.status == "insufficient_evidence":
        return InsufficientEvidence(
            reason=reason, evidence_count=len(evidence), reason_code=code, evidence=evidence
        )
    if row.status == "unverified_revision":
        return UnverifiedRevision(reason=reason, reason_code=code, evidence=evidence)
    if row.status == "incompatible_basis":
        failed = tuple(k for k, v in sorted(row.check_outcomes.items()) if v == "failed")
        return IncompatibleBasis(
            reason=reason, reason_code=code, evidence=evidence, failed_checks=failed
        )
    raise ValueError(f"unknown stored status {row.status!r}")  # CHECK makes this unreachable


def _row_as_of(
    session: Session,
    generation: MetricGenerationRecord,
    start: date,
    end: date,
    as_of: datetime,
) -> MetricResultRecord | None:
    # Deliberately no filter on status: the latest epoch wins, uncertain or not.
    versions = VersionPair(generation.calculation_version, generation.selection_policy_version)
    return session.execute(
        select(MetricResultRecord)
        .where(
            *_series(generation.entity_id, generation.metric_id, versions),
            MetricResultRecord.generation_id <= generation.id,
            MetricResultRecord.start_date == start,
            MetricResultRecord.period_end_date == end,
            MetricResultRecord.effective_from <= as_of,
        )
        .order_by(MetricResultRecord.effective_from.desc())
        .limit(1)
    ).scalar_one_or_none()


def _answer(
    session: Session, generation: MetricGenerationRecord, start: date, end: date, as_of: datetime
) -> MetricReadResult:
    row = _row_as_of(session, generation, start, end, as_of)
    if row is None:
        return Unknown(reason=f"no result effective as of {as_of.isoformat()}")
    return _typed(session, row)


def read_pinned(
    session: Session,
    entity_id: str,
    metric_id: str,
    start: date,
    end: date,
    as_of: datetime,
    *,
    versions: VersionPair,
    generation_id: int,
) -> MetricReadResult:
    """M1: exactly what generation `generation_id` answered, forever. The
    generation must belong to the given entity, metric and version pair."""
    as_of = _require_aware(as_of, "as_of")
    generation = session.get(MetricGenerationRecord, generation_id)
    if generation is None or (
        generation.entity_id,
        generation.metric_id,
        generation.calculation_version,
        generation.selection_policy_version,
    ) != (entity_id, metric_id, versions.calculation_version, versions.selection_policy_version):
        raise ValueError(
            f"generation {generation_id} does not belong to {entity_id} {metric_id} {versions}"
        )
    return _answer(session, generation, start, end, as_of)


def _behind_inputs(
    session: Session,
    generation: MetricGenerationRecord,
    spec: MetricSpec,
    policy: SelectionPolicy,
    start: date,
    end: date,
    as_of: datetime,
) -> list[str]:
    """Why `generation` cannot answer for (period, as_of): inputs available
    by `as_of` that it never saw. Empty list = up to date for this query."""
    reasons: list[str] = []
    period_match = (
        FinancialDataPointRecord.entity_id == generation.entity_id,
        FinancialDataPointRecord.metric_id.in_(_metric_tags(spec, policy)),
        FinancialDataPointRecord.epistemic_label == "fact",
        FinancialDataPointRecord.start_date == start,
        FinancialDataPointRecord.period_end_date == end,
        FinancialDataPointRecord.known_available_at <= as_of,
    )
    newer_rows = session.execute(
        select(FinancialDataPointRecord).where(
            *period_match,
            FinancialDataPointRecord.id > generation.fact_watermark,
            FinancialDataPointRecord.id.not_in(select(FactQuarantineRecord.fact_id)),
        )
    ).scalars()
    newer = relevant_facts(
        spec,
        policy,
        [
            FactView(
                fact_id=r.id, metric_id=r.metric_id, unit=r.unit, start_date=r.start_date,
                end_date=r.period_end_date, value=Decimal(0), accession_number="",
                form_type=r.source_form_type, known_available_at=as_of,
                availability_precision="",
            )
            for r in newer_rows
        ],
    )
    if newer:
        reasons.append(f"{len(newer)} newer fact(s)")
    newly_quarantined = session.execute(
        select(func.count())
        .select_from(FactQuarantineRecord)
        .join(FinancialDataPointRecord, FinancialDataPointRecord.id == FactQuarantineRecord.fact_id)
        .where(*period_match, FactQuarantineRecord.id > generation.quarantine_watermark)
    ).scalar()
    if newly_quarantined:
        reasons.append(f"{newly_quarantined} newly quarantined fact(s)")
    filings = session.execute(
        select(FilingRecord).where(
            FilingRecord.entity_id == generation.entity_id,
            FilingRecord.id > generation.filing_watermark,
            FilingRecord.form_type.in_(policy.recast_forms),
            FilingRecord.known_available_at <= as_of,
            FilingRecord.filing_date > end,
        )
    ).scalars()
    for f in filings:
        if f.items and policy.non_reliance_item in [i.strip() for i in f.items.split(",")]:
            reasons.append(f"newer non-reliance filing {f.accession_number}")
    return reasons


def read_current(
    session: Session,
    entity_id: str,
    metric_id: str,
    start: date,
    end: date,
    as_of: datetime,
    *,
    versions: VersionPair,
) -> MetricReadResult:
    """M2: current knowledge for an explicit version pair (no default pair,
    no fallback to another pair). Refuses to answer from a generation that
    is behind inputs already available as of `as_of`."""
    as_of = _require_aware(as_of, "as_of")
    policy, spec, _ = _resolve_versions(metric_id, versions)
    generation = session.execute(
        select(MetricGenerationRecord)
        .where(*_generations(entity_id, metric_id, versions))
        .order_by(MetricGenerationRecord.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if generation is None:
        return Unknown(reason=f"no generation built for {versions}")
    behind = _behind_inputs(session, generation, spec, policy, start, end, as_of)
    if behind:
        return UnverifiedRevision(
            reason=f"generation {generation.id} is behind inputs available as of "
            f"{as_of.isoformat()}: {'; '.join(behind)}",
            reason_code=UncertaintyReason.GENERATION_BEHIND_INPUTS,
        )
    return _answer(session, generation, start, end, as_of)


def read_at_system_time(
    session: Session,
    entity_id: str,
    metric_id: str,
    start: date,
    end: date,
    as_of: datetime,
    *,
    versions: VersionPair,
    system_time: datetime,
) -> MetricReadResult:
    """M3: what the latest generation existing at `system_time` answered,
    for an explicit version pair. (Use `active_versions(..., system_time)`
    to look up which pair was designated active then.)"""
    as_of = _require_aware(as_of, "as_of")
    system_time = _require_aware(system_time, "system_time")
    _resolve_versions(metric_id, versions)
    generation = session.execute(
        select(MetricGenerationRecord)
        .where(
            *_generations(entity_id, metric_id, versions),
            MetricGenerationRecord.created_at <= system_time,
        )
        .order_by(MetricGenerationRecord.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if generation is None:
        return Unknown(reason=f"no generation for {versions} existed at {system_time.isoformat()}")
    return _answer(session, generation, start, end, as_of)


def require_values(
    results: Sequence[MetricReadResult], *, skip_uncertain: bool = False
) -> list[Decimal]:
    """Numbers from a series, refusing uncertainty unless explicitly skipped,
    and refusing any mix of version pairs."""
    pairs = {r.versions for r in results if isinstance(r, ResolvedMetric)}
    if len(pairs) > 1:
        raise ValueError(f"series mixes version pairs: {sorted(map(str, pairs))}")
    uncertain = [r for r in results if is_uncertain(r)]
    if uncertain and not skip_uncertain:
        raise UncertainResultError(f"{len(uncertain)} of {len(results)} results are uncertain")
    return [r.value for r in results if isinstance(r, ResolvedMetric)]


__all__ = [
    "BuildOutcome",
    "InputIntegrityError",
    "InputMutationError",
    "MetricReadResult",
    "NoActiveVersionError",
    "PolicyManifestMismatchError",
    "ResolvedMetric",
    "ResultDriftError",
    "ResultStoreError",
    "RetroactiveDriftError",
    "UncertainResultError",
    "VersionPair",
    "activate_versions",
    "active_versions",
    "build_generation",
    "quarantine_fact",
    "read_at_system_time",
    "read_current",
    "read_pinned",
    "require_values",
]
