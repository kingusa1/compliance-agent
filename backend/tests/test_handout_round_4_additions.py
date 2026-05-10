"""Tests for the round-4 additions from supplier-spec-handout.pdf:

1. 5 new Critical phrase patterns (vulnerability, false-employment,
   VAT-inclusion misstatement, savings misrep, guaranteed rates).
2. Supplier alias canonicalisation map (§3.5a).
3. Rejection origin / fix action / workflow state enums (§3.6a).
4. Categoriser normalization for typo'd corpus values.
"""
from __future__ import annotations

import pytest

from app.watt_compliance.phrase_regex import scan
from app.watt_compliance.script_detect import canonicalize_supplier
from app.watt_compliance.taxonomy import (
    ALLOWED_WORKFLOW_TRANSITIONS,
    DEFAULT_FIX_ACTION_FOR_ORIGIN,
    FixAction,
    RejectionCategory,
    RejectionOrigin,
    Supplier,
    WorkflowState,
    normalize_category,
)


# ─── 1. New Critical phrase patterns ─────────────────────────────────────

class TestVulnerabilityCritical:
    def test_illness_signal_fires(self) -> None:
        t = "Hi this is Watt Utilities — I'm not well today, can we do this another time?"
        hits = scan(t, call_type="closer")
        assert any(h.rule_id == "C2-01" for h in hits)

    def test_language_barrier_fires(self) -> None:
        t = "Hello, Watt Utilities here. My english is not good, can you speak slower?"
        hits = scan(t, call_type="closer")
        assert any(h.rule_id == "C2-01" for h in hits)

    def test_no_vulnerability_signal_no_fire(self) -> None:
        t = "Hi, Watt Utilities here, the unit rate is 22 pence per kilowatt hour."
        hits = scan(t, call_type="closer")
        assert not any(h.rule_id == "C2-01" for h in hits)


class TestFalseEmploymentClaim:
    def test_direct_agreement_fires(self) -> None:
        t = "Hi from Watt Utilities — we have a direct agreement with E.ON."
        hits = scan(t, call_type="closer")
        assert any(h.rule_id == "CP-IDENTITY-FALSE-EMPLOY" for h in hits)

    def test_partnership_with_supplier_fires(self) -> None:
        t = "We have a partnership with British Gas to lock in fixed rates."
        hits = scan(t, call_type="closer")
        assert any(h.rule_id == "CP-IDENTITY-FALSE-EMPLOY" for h in hits)

    def test_clean_call_no_fire(self) -> None:
        t = "Watt Utilities is independent — we negotiate with British Gas on your behalf."
        hits = scan(t, call_type="closer")
        assert not any(h.rule_id == "CP-IDENTITY-FALSE-EMPLOY" for h in hits)


class TestVatInclusionMis:
    def test_prices_include_vat_fires(self) -> None:
        t = "Hi from Watt Utilities — prices include VAT and CCL on this contract."
        hits = scan(t, call_type="closer")
        assert any(h.rule_id == "CP-PRICE-VAT-INCLUSION-MIS" for h in hits)

    def test_inclusive_of_green_deal_fires(self) -> None:
        t = "Hi Watt Utilities — the rates are inclusive of Green Deal levy."
        hits = scan(t, call_type="closer")
        assert any(h.rule_id == "CP-PRICE-VAT-INCLUSION-MIS" for h in hits)

    def test_exclusive_disclosure_no_fire(self) -> None:
        t = "Hi Watt Utilities — the unit rate is exclusive of VAT and CCL."
        hits = scan(t, call_type="closer")
        assert not any(h.rule_id == "CP-PRICE-VAT-INCLUSION-MIS" for h in hits)


class TestSavingsMisrep:
    def test_standing_charge_savings_fires(self) -> None:
        t = "Watt Utilities here — most of the savings will come from the standing charge."
        hits = scan(t, call_type="closer")
        assert any(h.rule_id == "CP-MISSELL-SAVINGS-MISREP" for h in hits)

    def test_unit_rate_doesnt_matter_fires(self) -> None:
        t = "Watt Utilities — the unit rate wont matter much because of the standing charge."
        hits = scan(t, call_type="closer")
        assert any(h.rule_id == "CP-MISSELL-SAVINGS-MISREP" for h in hits)


class TestGuaranteedRates:
    def test_rates_guaranteed_for_3_years_fires(self) -> None:
        t = "Hi Watt Utilities — the rates are guaranteed fixed for 3 years."
        hits = scan(t, call_type="closer")
        assert any(h.rule_id == "CP-MISSELL-GUARANTEED-RATES" for h in hits)

    def test_locked_for_full_term_fires(self) -> None:
        t = "Watt Utilities here — unit rates locked for the full term."
        hits = scan(t, call_type="closer")
        assert any(h.rule_id == "CP-MISSELL-GUARANTEED-RATES" for h in hits)


# ─── 2. Supplier alias canonicalisation ──────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("BGL", Supplier.BGL),
    ("BG Lite", Supplier.BGL),
    ("british gas lite", Supplier.BGL),
    ("british gas", Supplier.BRITISH_GAS),
    ("BG CORE", Supplier.BRITISH_GAS),
    ("british gas business", Supplier.BRITISH_GAS),
    ("british gas buisness", Supplier.BRITISH_GAS),  # tracker typo
    ("E.on NEXT", Supplier.EON_NEXT),
    ("EON Next", Supplier.EON_NEXT),
    ("E.ON Energy Solutions Ltd", Supplier.EON_NEXT),
    ("EDF", Supplier.EDF),
    ("EDF Energy", Supplier.EDF),
    ("Pozitive", Supplier.POZITIVE),
    ("Scottish Power", Supplier.SCOTTISH_POWER),
    ("scottishpower", Supplier.SCOTTISH_POWER),
])
def test_canonicalize_supplier_known_aliases(raw: str, expected: Supplier) -> None:
    assert canonicalize_supplier(raw) == expected


def test_canonicalize_supplier_unknown_returns_none() -> None:
    assert canonicalize_supplier("Some Random Supplier Ltd") is None
    assert canonicalize_supplier("") is None
    assert canonicalize_supplier(None) is None


# ─── 3. Rejection origin / fix action / workflow state enums ─────────────

def test_rejection_origin_has_17_values() -> None:
    # Spec §3.6a: 17 origin values for the locked enum.
    assert len(list(RejectionOrigin)) == 17


def test_fix_action_has_15_values() -> None:
    assert len(list(FixAction)) == 15


def test_default_fix_action_covers_every_origin() -> None:
    for origin in RejectionOrigin:
        assert origin in DEFAULT_FIX_ACTION_FOR_ORIGIN, f"missing default fix for {origin}"


def test_workflow_state_machine_dead_is_terminal() -> None:
    assert ALLOWED_WORKFLOW_TRANSITIONS[WorkflowState.DEAD] == set()


def test_workflow_state_machine_fixed_and_approved_is_terminal() -> None:
    assert ALLOWED_WORKFLOW_TRANSITIONS[WorkflowState.FIXED_AND_APPROVED] == set()


def test_workflow_state_machine_not_started_to_in_progress_allowed() -> None:
    assert WorkflowState.IN_PROGRESS in ALLOWED_WORKFLOW_TRANSITIONS[WorkflowState.NOT_STARTED]


def test_workflow_state_machine_dead_reachable_from_every_non_terminal_state() -> None:
    for state, edges in ALLOWED_WORKFLOW_TRANSITIONS.items():
        if state in {WorkflowState.DEAD, WorkflowState.FIXED_AND_APPROVED}:
            continue
        assert WorkflowState.DEAD in edges, f"{state} can't transition to DEAD"


# ─── 4. Categoriser normalization ────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("process failure", RejectionCategory.PROCESS_FAILURE),
    ("priocess failure", RejectionCategory.PROCESS_FAILURE),       # typo
    ("PROCESS_FAILURE", RejectionCategory.PROCESS_FAILURE),
    ("Verbal Sales Error", RejectionCategory.VERBAL_SALES_ERROR),
    ("Admin Error", RejectionCategory.ADMIN_ERROR),
    ("DOCUSIGN ERROR", RejectionCategory.ADMIN_ERROR),
    ("DOCUISGN ERROR", RejectionCategory.ADMIN_ERROR),             # typo
    ("Compliance Issue", RejectionCategory.COMPLIANCE_ISSUE),
    ("Compliance Error", RejectionCategory.COMPLIANCE_ISSUE),
])
def test_normalize_category_known_variants(raw: str, expected: RejectionCategory) -> None:
    assert normalize_category(raw) == expected


def test_normalize_category_unknown_returns_none() -> None:
    assert normalize_category("Some Random Category") is None
    assert normalize_category("") is None
    assert normalize_category(None) is None
