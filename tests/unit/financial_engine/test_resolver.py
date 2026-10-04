"""Pure resolver behaviour (approved Phase 2B policy), on synthetic facts.

Each test states one rule of the single-basis policy. No database.
"""

from __future__ import annotations

import itertools
from datetime import date
from decimal import Decimal, getcontext, localcontext

from ibi.core.types import UncertaintyReason
from ibi.financial_engine.formulas import FORMULAS
from ibi.financial_engine.policy import POLICY_V1
from ibi.financial_engine.resolver import compute_history, input_set_hash, resolve

from .builders import COST, FY23, GP, REV, REV_ALT, T0, at, event_8k, fact, gm_filing

GM = POLICY_V1.metric("ibi:gross_margin")
GM_F = FORMULAS[("ibi:gross_margin", "1")]


def _resolve(facts, as_of=None, events=(), recast_evidence=None):
    return resolve(
        GM, POLICY_V1, GM_F, FY23, facts, list(events), as_of or at(1000),
        recast_evidence=recast_evidence,
    )


CLASSIFIED = frozenset({"xbrl_instance_restatement_classified"})


def _relations(r):
    return sorted((e.relation, e.accession_number) for e in r.evidence)


# --- basis selection -----------------------------------------------------


def test_single_periodic_basis_yields_value():
    r = _resolve(gm_filing("A1", "10-K", 1000, 600))
    assert r.status == "value"
    assert r.value == Decimal("0.4000000000")
    assert r.basis_accession == "A1"
    assert r.reason_code is None
    assert dict(r.check_outcomes)["gross_profit_equals_revenue_minus_cost"] == "not_applicable"
    assert {e.relation for e in r.evidence} == {"input"}


def test_missing_input_is_insufficient():
    r = _resolve([fact(REV, 1000, "A1", "10-K")])
    assert r.status == "insufficient_evidence"
    assert r.reason_code == UncertaintyReason.MISSING_INPUT


def test_inputs_are_never_combined_across_filings():
    facts = [fact(REV, 1000, "A1", "10-K"), fact(COST, 600, "A2", "10-Q", at(1))]
    r = _resolve(facts)
    assert r.status == "insufficient_evidence"
    assert r.reason_code == UncertaintyReason.NO_SINGLE_BASIS


def test_complete_8k_is_not_a_basis_without_classification_evidence():
    """A complete set of tags in one 8-K is NOT proof of a compatible recast."""
    r = _resolve(gm_filing("E1", "8-K", 1000, 600))
    assert r.status == "insufficient_evidence"
    assert r.reason_code == UncertaintyReason.NO_SINGLE_BASIS


def test_unit_mismatch_is_ignored():
    facts = [fact(REV, 1000, "A1", "10-K"), fact(COST, 600, "A1", "10-K", unit="EUR")]
    assert _resolve(facts).reason_code == UncertaintyReason.MISSING_INPUT


def test_facts_after_as_of_are_invisible():
    facts = gm_filing("A1", "10-K", 1000, 600) + gm_filing("E1", "8-K", 1000, 650, at(10))
    assert _resolve(facts, as_of=at(5)).status == "value"
    assert _resolve(facts, as_of=at(10)).status == "unverified_revision"


# --- divergence and classification --------------------------------------


def test_divergent_complete_8k_is_unverified_revision():
    facts = gm_filing("A1", "10-K", 1000, 600) + gm_filing("E1", "8-K", 1000, 650, at(10))
    r = _resolve(facts)
    assert r.status == "unverified_revision"
    assert r.reason_code == UncertaintyReason.UNCLASSIFIED_8K_DIVERGENCE
    assert r.basis_accession == "A1"
    assert ("unverified_revision", "E1") in _relations(r)
    assert r.value is None


def test_equal_8k_only_corroborates():
    base = gm_filing("A1", "10-K", 1000, 600)
    confirmed = base + gm_filing("E1", "8-K", 1000, 600, at(10))
    r0, r1 = _resolve(base), _resolve(confirmed)
    assert r1.status == "value" and r1.value == r0.value
    assert ("corroborating", "E1") in _relations(r1)
    assert r1.result_hash != r0.result_hash  # a confirmation is a new epoch (C2)


def test_unclassified_and_null_forms_fail_closed():
    for form in ("S-4", None):
        facts = gm_filing("A1", "10-K", 1000, 600) + [fact(COST, 601, "X1", form, at(3))]
        r = _resolve(facts)
        assert r.reason_code == UncertaintyReason.UNCLASSIFIED_FORM_DIVERGENCE, form


def test_partial_periodic_divergence_fails_closed():
    facts = gm_filing("A1", "10-K", 1000, 600) + [fact(REV, 1100, "A2", "10-Q/A", at(3))]
    r = _resolve(facts)
    assert r.reason_code == UncertaintyReason.PARTIAL_PERIODIC_DIVERGENCE


def test_complete_amendment_becomes_basis_and_is_annotated():
    facts = gm_filing("A1", "10-K", 1000, 600) + gm_filing("A2", "10-K/A", 1000, 500, at(30))
    r = _resolve(facts)
    assert r.status == "value" and r.value == Decimal("0.5000000000")
    assert r.basis_accession == "A2"
    assert r.revision_kind == "amendment"
    assert ("superseded_basis", "A1") in _relations(r)


def test_later_periodic_comparative_revision_is_annotated_unexplained():
    facts = gm_filing("A1", "10-K", 1000, 600) + gm_filing("A9", "10-K", 1200, 600, at(365))
    r = _resolve(facts)
    assert r.basis_accession == "A9"
    assert r.revision_kind == "comparative_revision_unexplained"


def test_later_periodic_with_equal_values_has_no_revision_kind():
    facts = gm_filing("A1", "10-K", 1000, 600) + gm_filing("A9", "10-K", 1000, 600, at(365))
    r = _resolve(facts)
    assert r.basis_accession == "A9" and r.revision_kind is None


# --- 8-K eligibility (evidence-qualified recasts) -------------------------


def _recast_8k(form="8-K", gp=None, **kw):
    facts = gm_filing("R1", form, 1000, 650, at(10), **kw)
    if gp is not None:
        facts.append(fact(GP, gp, "R1", form, at(10)))
    return facts


def test_classified_consistent_complete_8k_becomes_basis_as_recast():
    facts = gm_filing("A1", "10-K", 1000, 600) + _recast_8k(gp=350)
    r = _resolve(facts, recast_evidence={"R1": CLASSIFIED})
    assert r.status == "value" and r.value == Decimal("0.3500000000")
    assert r.basis_accession == "R1"
    assert r.revision_kind == "recast"
    assert ("superseded_basis", "A1") in _relations(r)


def test_classified_8k_a_is_eligible_too():
    facts = gm_filing("A1", "10-K", 1000, 600) + _recast_8k(form="8-K/A", gp=350)
    assert _resolve(facts, recast_evidence={"R1": CLASSIFIED}).basis_accession == "R1"


def test_classified_8k_without_evaluable_identity_check_is_not_eligible():
    """Consistency cannot be demonstrated without the gross-profit identity."""
    facts = gm_filing("A1", "10-K", 1000, 600) + _recast_8k(gp=None)
    r = _resolve(facts, recast_evidence={"R1": CLASSIFIED})
    assert r.reason_code == UncertaintyReason.UNCLASSIFIED_8K_DIVERGENCE
    assert r.basis_accession == "A1"


def test_classified_8k_failing_identity_check_is_not_eligible():
    facts = gm_filing("A1", "10-K", 1000, 600) + _recast_8k(gp=351)
    r = _resolve(facts, recast_evidence={"R1": CLASSIFIED})
    assert r.reason_code == UncertaintyReason.UNCLASSIFIED_8K_DIVERGENCE


def test_classified_but_partial_8k_is_not_a_basis():
    facts = gm_filing("A1", "10-K", 1000, 600) + [fact(COST, 650, "R1", "8-K", at(10))]
    r = _resolve(facts, recast_evidence={"R1": CLASSIFIED})
    assert r.reason_code == UncertaintyReason.UNCLASSIFIED_8K_DIVERGENCE


def test_evidence_for_another_accession_does_not_qualify():
    facts = gm_filing("A1", "10-K", 1000, 600) + _recast_8k(gp=350)
    r = _resolve(facts, recast_evidence={"OTHER": CLASSIFIED})
    assert r.reason_code == UncertaintyReason.UNCLASSIFIED_8K_DIVERGENCE


def test_evidence_never_qualifies_other_forms():
    facts = gm_filing("A1", "10-K", 1000, 600) + _recast_8k(form="S-4", gp=350)
    r = _resolve(facts, recast_evidence={"R1": CLASSIFIED})
    assert r.reason_code == UncertaintyReason.UNCLASSIFIED_FORM_DIVERGENCE


def test_recast_evidence_is_part_of_the_snapshot_identity():
    facts = _recast_8k(gp=350)
    assert input_set_hash(GM, POLICY_V1, facts, []) != input_set_hash(
        GM, POLICY_V1, facts, [], recast_evidence={"R1": CLASSIFIED}
    )


# --- conflicts -------------------------------------------------------------


def test_disagreeing_tags_in_basis_conflict():
    facts = gm_filing("A1", "10-K", 1000, 600) + [fact(REV_ALT, 1001, "A1", "10-K")]
    r = _resolve(facts)
    assert r.status == "conflicting_evidence"
    assert r.reason_code == UncertaintyReason.TAG_DISAGREEMENT


def test_agreeing_tags_use_priority_tag():
    facts = gm_filing("A1", "10-K", 1000, 600) + [fact(REV_ALT, 1000, "A1", "10-K")]
    r = _resolve(facts)
    assert r.status == "value"
    inputs = [e for e in r.evidence if e.relation == "input" and e.role == "revenue"]
    assert [e.metric_id for e in inputs] == [REV]


def test_simultaneous_filings_with_different_values_conflict():
    facts = gm_filing("A1", "10-K", 1000, 600) + gm_filing("A2", "10-K", 1000, 610)
    r = _resolve(facts)
    assert r.reason_code == UncertaintyReason.SIMULTANEOUS_DIVERGENCE


def test_simultaneous_filings_with_equal_values_pick_highest_accession():
    facts = gm_filing("A1", "10-K", 1000, 600) + gm_filing("A2", "10-K", 1000, 600)
    r = _resolve(facts)
    assert r.status == "value" and r.basis_accession == "A2"
    assert ("corroborating", "A1") in _relations(r)


# --- identity checks, non-reliance, formula -------------------------------


def test_identity_check_passes_and_is_recorded():
    facts = gm_filing("A1", "10-K", 1000, 600) + [fact(GP, 400, "A1", "10-K")]
    r = _resolve(facts)
    assert r.status == "value"
    assert dict(r.check_outcomes)["gross_profit_equals_revenue_minus_cost"] == "passed"
    assert any(e.relation == "check_term" for e in r.evidence)


def test_identity_violation_is_incompatible_basis():
    facts = gm_filing("A1", "10-K", 1000, 600) + [fact(GP, 401, "A1", "10-K")]
    r = _resolve(facts)
    assert r.status == "incompatible_basis"
    assert r.reason_code == UncertaintyReason.IDENTITY_VIOLATION


def test_non_reliance_after_basis_until_restated_basis_arrives():
    events = [event_8k("N1", "4.02,9.01", at(20), date(2024, 2, 21))]
    facts = gm_filing("A1", "10-K", 1000, 600)
    r = _resolve(facts, events=events)
    assert r.reason_code == UncertaintyReason.NON_RELIANCE_DECLARED
    assert _resolve(facts, as_of=at(19), events=events).status == "value"
    restated = facts + gm_filing("A2", "10-K/A", 1000, 550, at(40))
    assert _resolve(restated, events=events).status == "value"


def test_non_reliance_ignores_other_items_and_later_periods():
    facts = gm_filing("A1", "10-K", 1000, 600)
    assert _resolve(facts, events=[event_8k("N1", "2.02", at(20), date(2024, 2, 21))]).status == (
        "value"
    )
    # Filed before the period ended: cannot concern it.
    early = [event_8k("N2", "4.02", at(20), date(2023, 6, 1))]
    assert _resolve(facts, events=early).status == "value"


def test_zero_revenue_is_insufficient_with_basis_kept():
    r = _resolve(gm_filing("A1", "10-K", 0, 0))
    assert r.reason_code == UncertaintyReason.ZERO_DENOMINATOR
    assert r.basis_accession == "A1" and r.value is None


# --- determinism -------------------------------------------------------------


def test_result_is_independent_of_input_order():
    facts = (
        gm_filing("A1", "10-K", 1000, 600)
        + [fact(GP, 400, "A1", "10-K")]
        + gm_filing("E1", "8-K", 1000, 600, at(10))
    )
    hashes = {_resolve(list(p)).result_hash for p in itertools.permutations(facts)}
    assert len(hashes) == 1


def test_result_is_independent_of_global_decimal_context():
    facts = gm_filing("A1", "10-K", 3, 1)
    expected = _resolve(facts).value
    with localcontext() as ctx:
        ctx.prec = 5
        assert getcontext().prec == 5
        assert _resolve(facts).value == expected == Decimal("0.6666666667")


def test_scale_of_stored_values_does_not_change_hash():
    a = _resolve(gm_filing("A1", "10-K", "1000", "600"))
    b = _resolve(gm_filing("A1", "10-K", "1000.000000", "600.000000"))
    assert a.result_hash == b.result_hash


# --- epochs --------------------------------------------------------------------


def test_history_emits_one_row_per_change():
    facts = (
        gm_filing("A1", "10-K", 1000, 600)  # T0: value
        + gm_filing("E1", "8-K", 1000, 650, at(10))  # unverified revision
        + gm_filing("A9", "10-K", 1000, 650, at(365))  # restated comparative: value again
    )
    hist = compute_history(GM, POLICY_V1, GM_F, facts, [])
    assert [(h.effective_from, h.resolution.status) for h in hist] == [
        (T0, "value"),
        (at(10), "unverified_revision"),
        (at(365), "value"),
    ]
    assert hist[-1].resolution.revision_kind == "comparative_revision_unexplained"


def test_history_skips_epochs_that_change_nothing():
    facts = gm_filing("A1", "10-K", 1000, 600) + [
        fact("us-gaap:Assets", 5, "Z1", "10-Q", at(3))  # irrelevant concept
    ]
    assert len(compute_history(GM, POLICY_V1, GM_F, facts, [])) == 1


def test_history_excludes_ytd_and_records_precision():
    ytd = (date(2023, 1, 1), date(2023, 6, 30))  # 180 days: neither quarter nor annual
    facts = gm_filing("A1", "10-K", 1000, 600, precision="day_conservative") + gm_filing(
        "Q2", "10-Q", 500, 300, at(-100), period=ytd
    )
    hist = compute_history(GM, POLICY_V1, GM_F, facts, [])
    assert [(h.start_date, h.end_date) for h in hist] == [FY23]
    assert hist[0].availability_precision == "day_conservative"


def test_input_set_hash_tracks_only_relevant_inputs():
    facts = gm_filing("A1", "10-K", 1000, 600)
    h0 = input_set_hash(GM, POLICY_V1, facts, [])
    assert input_set_hash(GM, POLICY_V1, facts + [fact("us-gaap:Assets", 5, "Z", "10-Q")], []) == h0
    assert input_set_hash(GM, POLICY_V1, facts + [fact(GP, 400, "A1", "10-K")], []) != h0
