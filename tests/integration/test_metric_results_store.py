"""Phase 2B result store: generations, idempotency, drift guards, typed reads.

Runs on a temporary SQLite database always, and additionally on PostgreSQL
when `IBI_TEST_POSTGRES_URL` points at a dedicated, disposable test
database (its name must contain "test"). It deliberately never uses
`IBI_DATABASE_URL`: these tests create and drop tables.
"""

from __future__ import annotations

import dataclasses
import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import ibi.financial_engine.formulas as formulas_mod
import ibi.financial_engine.policy as policy_mod
from ibi.core.types import ConflictingEvidence, UncertaintyReason, Unknown, UnverifiedRevision
from ibi.db.base import Base
from ibi.db.models import (
    EntityRecord,
    FinancialDataPointRecord,
    MetricGenerationRecord,
    MetricResultInputRecord,
    MetricResultRecord,
)
from ibi.financial_engine import results_store as rs
from ibi.financial_engine.results_store import VersionPair

ENTITY = "CIK0000000001"
GM = "ibi:gross_margin"
FY23 = (date(2023, 1, 1), date(2023, 12, 31))
FY22 = (date(2022, 1, 1), date(2022, 12, 31))
T0 = datetime(2024, 2, 1, 15, 0, tzinfo=UTC)
REV = "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
COST = "us-gaap:CostOfGoodsAndServicesSold"
V1 = VersionPair("1", "1")


def at(days: float) -> datetime:
    return T0 + timedelta(days=days)


def _backends():
    yield "sqlite"
    yield pytest.param("postgresql", marks=pytest.mark.integration)


@pytest.fixture(params=list(_backends()))
def session(request, tmp_path):
    if request.param == "sqlite":
        engine = create_engine(f"sqlite:///{tmp_path / 'store.db'}")
    else:
        url = os.environ.get("IBI_TEST_POSTGRES_URL")
        if not url:
            pytest.skip("IBI_TEST_POSTGRES_URL not set (dedicated PostgreSQL test database)")
        if "test" not in (make_url(url).database or ""):
            pytest.skip("IBI_TEST_POSTGRES_URL database name must contain 'test'")
        engine = create_engine(url, connect_args={"connect_timeout": 3})
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(EntityRecord(entity_id=ENTITY, kind="company", canonical_name="Test Co"))
        s.commit()
        yield s
    Base.metadata.drop_all(engine)
    engine.dispose()


def add_fact(s, tag, value, accn, form, when, period=FY23, kaa_none=False) -> int:
    row = FinancialDataPointRecord(
        entity_id=ENTITY,
        metric_id=tag,
        unit="USD",
        start_date=period[0],
        period_end_date=period[1],
        value=Decimal(str(value)),
        epistemic_label="fact",
        accession_number=accn,
        known_available_at=None if kaa_none else when,
        availability_precision=None if kaa_none else "acceptance_timestamp",
        source_form_type=form,
    )
    s.add(row)
    s.flush()
    return row.id


def add_gm(s, accn, form, rev, cost, when, period=FY23):
    return [
        add_fact(s, REV, rev, accn, form, when, period),
        add_fact(s, COST, cost, accn, form, when, period),
    ]


def build(s, versions=V1):
    outcome = rs.build_generation(s, ENTITY, GM, versions)
    s.commit()
    return outcome


def count(s, model) -> int:
    return s.execute(select(func.count()).select_from(model)).scalar()


def current(s, as_of, period=FY23, versions=V1):
    return rs.read_current(s, ENTITY, GM, *period, as_of, versions=versions)


def pinned(s, generation_id, as_of, versions=V1):
    return rs.read_pinned(
        s, ENTITY, GM, *FY23, as_of, versions=versions, generation_id=generation_id
    )


# --- builds -----------------------------------------------------------------


def test_initial_build_and_typed_read(session):
    add_gm(session, "A1", "10-K", 1000, 600, T0)
    out = build(session)
    assert (out.status, out.kind, out.rows_written) == ("created", "initial", 1)

    r = current(session, at(1))
    assert isinstance(r, rs.ResolvedMetric)
    assert r.value == Decimal("0.4") and r.basis_accession == "A1"
    assert r.versions == V1
    assert {(e.role, e.relation) for e in r.evidence} == {
        ("revenue", "input"), ("cost_of_revenue", "input")
    }
    assert count(session, MetricResultInputRecord) == 2
    assert isinstance(current(session, at(-1)), Unknown)  # before the first epoch


def test_rebuild_over_unchanged_snapshot_writes_nothing(session):
    add_gm(session, "A1", "10-K", 1000, 600, T0)
    first = build(session)
    before = (count(session, MetricGenerationRecord), count(session, MetricResultRecord))
    second = build(session)
    assert second.status == "unchanged" and second.generation_id == first.generation_id
    assert (count(session, MetricGenerationRecord), count(session, MetricResultRecord)) == before


def test_new_vintage_writes_only_new_epochs_and_old_view_is_unchanged(session):
    """C2: unchanged history is never copied into the new generation."""
    add_gm(session, "A1", "10-K", 1000, 600, T0)
    add_gm(session, "B1", "10-K", 900, 500, at(-365), period=FY22)
    g1 = build(session).generation_id
    assert count(session, MetricResultRecord) == 2

    add_gm(session, "E1", "8-K", 1000, 650, at(10))  # touches FY23 only
    out = build(session)
    assert (out.kind, out.rows_written) == ("new_vintage", 1)
    assert count(session, MetricResultRecord) == 3  # not 2 + 3

    late = current(session, at(20))
    assert isinstance(late, UnverifiedRevision)
    assert late.reason_code is UncertaintyReason.UNCLASSIFIED_8K_DIVERGENCE
    assert {e.accession_number for e in late.evidence if e.relation == "unverified_revision"} == {
        "E1"
    }
    assert isinstance(current(session, at(9)), rs.ResolvedMetric)
    assert current(session, at(20), period=FY22).value == Decimal("0.4444444444")
    # M1: the earlier generation still answers exactly as it did.
    assert pinned(session, g1, at(20)).value == Decimal("0.4")


def test_later_confirmation_creates_a_new_epoch(session):
    add_gm(session, "A1", "10-K", 1000, 600, T0)
    build(session)
    add_gm(session, "E1", "8-K", 1000, 600, at(10))
    assert build(session).rows_written == 1
    r = current(session, at(11))
    assert r.value == Decimal("0.4")
    assert ("E1", "corroborating") in {(e.accession_number, e.relation) for e in r.evidence}


def test_uncertain_epoch_is_never_skipped_for_an_older_value(session):
    add_gm(session, "A1", "10-K", 1000, 600, T0)
    build(session)
    add_fact(session, REV, 1100, "A2", "10-Q/A", at(30))  # later partial-periodic divergence
    build(session)
    r = current(session, at(40))
    assert isinstance(r, UnverifiedRevision)
    assert r.reason_code is UncertaintyReason.PARTIAL_PERIODIC_DIVERGENCE
    with pytest.raises(rs.UncertainResultError):
        rs.require_values([r])
    assert rs.require_values([r], skip_uncertain=True) == []
    assert current(session, at(29)).value == Decimal("0.4")


def test_simultaneous_divergence_is_typed_conflict(session):
    add_gm(session, "A1", "10-K", 1000, 600, T0)
    add_gm(session, "A2", "10-K", 1000, 610, T0)
    build(session)
    r = current(session, at(1))
    assert isinstance(r, ConflictingEvidence)
    assert r.reason_code is UncertaintyReason.SIMULTANEOUS_DIVERGENCE
    assert set(r.candidate_values) == {Decimal("600"), Decimal("610"), Decimal("1000")}


# --- explicit version pairs -----------------------------------------------------


def test_reads_require_and_respect_an_explicit_version_pair(session, monkeypatch):
    add_gm(session, "A1", "10-K", 1000, 600, T0)
    g1 = build(session).generation_id
    v2 = VersionPair("1", "2-test")
    monkeypatch.setitem(
        policy_mod.POLICIES, "2-test", dataclasses.replace(policy_mod.POLICY_V1, version="2-test")
    )
    assert isinstance(current(session, at(1), versions=v2), Unknown)  # never falls back to V1
    with pytest.raises(ValueError):
        pinned(session, g1, at(1), versions=v2)  # generation belongs to another pair
    with pytest.raises(TypeError):
        rs.read_current(session, ENTITY, GM, *FY23, at(1))  # pair is mandatory
    build(session, v2)
    a, b = current(session, at(1)), current(session, at(1), versions=v2)
    with pytest.raises(ValueError):
        rs.require_values([a, b])  # a series must never mix pairs


def test_system_time_replay_and_active_version_lookup(session):
    with pytest.raises(rs.NoActiveVersionError):
        rs.active_versions(session, GM)
    rs.activate_versions(session, GM, V1, note="phase 2b default")
    add_gm(session, "A1", "10-K", 1000, 600, T0)
    build(session)
    system_time = datetime.now(UTC)
    add_gm(session, "E1", "8-K", 1000, 650, at(10))
    build(session)

    pair_then = rs.active_versions(session, GM, system_time)
    then = rs.read_at_system_time(
        session, ENTITY, GM, *FY23, at(20), versions=pair_then, system_time=system_time
    )
    assert isinstance(then, rs.ResolvedMetric) and then.value == Decimal("0.4")
    assert isinstance(current(session, at(20), versions=rs.active_versions(session, GM)),
                      UnverifiedRevision)
    with pytest.raises(rs.NoActiveVersionError):  # nothing was active a day earlier
        rs.active_versions(session, GM, system_time - timedelta(days=1))
    before_any_build = rs.read_at_system_time(
        session, ENTITY, GM, *FY23, at(20), versions=V1,
        system_time=system_time - timedelta(days=1),
    )
    assert isinstance(before_any_build, Unknown)
    with pytest.raises(KeyError):
        rs.activate_versions(session, GM, VersionPair("1", "does-not-exist"))


# --- freshness guard (current-knowledge reads) --------------------------------


def test_reader_refuses_a_generation_behind_its_inputs(session):
    add_gm(session, "A1", "10-K", 1000, 600, T0)
    build(session)
    add_gm(session, "E1", "8-K", 1000, 650, at(10))  # ingested, not yet built
    behind = current(session, at(20))
    assert isinstance(behind, UnverifiedRevision)
    assert behind.reason_code is UncertaintyReason.GENERATION_BEHIND_INPUTS
    # Before the unseen fact was available, the generation is still exact.
    assert current(session, at(5)).value == Decimal("0.4")


# --- drift and integrity guards ---------------------------------------------------


def test_retroactive_input_is_blocked_and_needs_a_new_policy_version(session, monkeypatch):
    add_gm(session, "A1", "10-K", 1000, 600, T0)
    add_gm(session, "A9", "10-K", 1000, 600, at(365))
    build(session)
    rows_before = count(session, MetricResultRecord)
    # A fact discovered late, but publicly available before the last epoch.
    add_fact(session, COST, 640, "X1", "S-4", at(100))
    session.commit()
    with pytest.raises(rs.RetroactiveDriftError):
        build(session)
    session.rollback()
    assert count(session, MetricGenerationRecord) == 1
    assert count(session, MetricResultRecord) == rows_before
    # The blocked state is visible to current-knowledge readers.
    assert current(session, at(200)).reason_code is UncertaintyReason.GENERATION_BEHIND_INPUTS

    v2 = VersionPair("1", "2-test")
    monkeypatch.setitem(
        policy_mod.POLICIES, "2-test", dataclasses.replace(policy_mod.POLICY_V1, version="2-test")
    )
    assert build(session, v2).kind == "initial"
    r = current(session, at(200), versions=v2)
    assert r.reason_code is UncertaintyReason.UNCLASSIFIED_FORM_DIVERGENCE


def test_changed_formula_without_version_bump_is_drift(session, monkeypatch):
    add_gm(session, "A1", "10-K", 1000, 600, T0)
    build(session)
    key = (GM, "1")
    tampered = dataclasses.replace(formulas_mod.FORMULAS[key], compute=lambda x: Decimal("0.123"))
    monkeypatch.setitem(formulas_mod.FORMULAS, key, tampered)
    with pytest.raises(rs.ResultDriftError):
        build(session)


def test_changed_policy_content_under_same_version_is_detected(session, monkeypatch):
    add_gm(session, "A1", "10-K", 1000, 600, T0)
    build(session)
    edited = dataclasses.replace(policy_mod.POLICY_V1, non_reliance_item="4.01")
    monkeypatch.setitem(policy_mod.POLICIES, "1", edited)
    with pytest.raises(rs.PolicyManifestMismatchError):
        build(session)


def test_mutated_stored_fact_is_detected(session):
    ids = add_gm(session, "A1", "10-K", 1000, 600, T0)
    build(session)
    session.execute(text("UPDATE financial_data SET value = 601 WHERE id = :id"), {"id": ids[1]})
    session.commit()
    with pytest.raises(rs.InputMutationError):
        build(session)


def test_fact_without_availability_stops_the_build(session):
    add_fact(session, REV, 1000, "A1", "10-K", T0, kaa_none=True)
    with pytest.raises(rs.InputIntegrityError):
        rs.build_generation(session, ENTITY, GM, V1)


def test_quarantine_is_append_only_and_flags_stale_generations(session):
    ids = add_gm(session, "A1", "10-K", 1000, 600, T0)
    build(session)
    with pytest.raises(ValueError):
        rs.quarantine_fact(session, ids[0], "  ")
    rs.quarantine_fact(session, ids[0], "test: corrupted ingestion")
    session.commit()
    assert current(session, at(1)).reason_code is UncertaintyReason.GENERATION_BEHIND_INPUTS
    # Removing a fact from history is retroactive: blocked under the same version.
    with pytest.raises(rs.RetroactiveDriftError):
        build(session)


# --- database constraints -----------------------------------------------------------


def _generation(s) -> int:
    add_gm(s, "A1", "10-K", 1000, 600, T0)
    return build(s).generation_id


def _row(gid, **overrides):
    values = dict(
        generation_id=gid, entity_id=ENTITY, metric_id=GM, calculation_version="1",
        selection_policy_version="1", unit="pure", start_date=FY23[0],
        period_end_date=FY23[1], effective_from=at(50),
        availability_precision="acceptance_timestamp", status="value", reason_code=None,
        value=Decimal("0.4"), check_outcomes={}, result_hash="x" * 64,
        epistemic_label="calculation",
    )
    values.update(overrides)
    return MetricResultRecord(**values)


@pytest.mark.parametrize(
    "overrides",
    [
        {"value": None},  # value status without a value
        {"status": "insufficient_evidence"},  # uncertainty carrying a number
        {"status": "bogus", "value": None, "reason_code": "x"},
        {"epistemic_label": "fact"},
        {"revision_kind": "guess"},
        {"availability_precision": "approximate"},
    ],
)
def test_check_constraints_reject_inconsistent_rows(session, overrides):
    gid = _generation(session)
    session.add(_row(gid, **overrides))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_provenance_relation_is_constrained(session):
    gid = _generation(session)
    result_id = session.execute(select(MetricResultRecord.id)).scalar_one()
    fact_id = session.execute(select(FinancialDataPointRecord.id)).scalars().first()
    session.add(
        MetricResultInputRecord(result_id=result_id, fact_id=fact_id, role="x", relation="guess")
    )
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()
    assert gid


def test_one_result_per_epoch_per_version_pair(session):
    gid = _generation(session)
    existing = session.execute(select(MetricResultRecord)).scalar_one()
    session.add(_row(gid, effective_from=existing.effective_from))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_naive_timestamps_are_rejected(session):
    with pytest.raises(ValueError):
        current(session, datetime(2024, 1, 1))
