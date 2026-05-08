"""Tests for app.compliance.taxonomy — the canonical 4 categories +
27 rejection reasons + 8 standards source of truth.

These tests pin down the contract so future edits can't silently drop a
reason code or move a reason between categories without breaking CI.
"""
from __future__ import annotations

import pytest

from app.watt_compliance.taxonomy import (
    REJECTION_REASONS,
    REJECTION_REASONS_BY_CODE,
    SEVERITY_TO_ACTION,
    SUPPLIER_LABELS,
    WATT_STANDARDS,
    CallType,
    RejectionCategory,
    RejectionReason,
    Severity,
    Supplier,
    VerdictAction,
    reasons_for_category,
    reasons_for_standard,
)


def test_27_rejection_reasons_exact_count():
    """Pinned at 27 (one per Standard-1..8 named failure mode)."""
    assert len(REJECTION_REASONS) == 27


def test_rejection_codes_are_unique_and_well_formed():
    codes = [r.code for r in REJECTION_REASONS]
    assert len(codes) == len(set(codes)), "duplicate rejection code"
    for code in codes:
        assert code.startswith("R") and code[1:].isdigit() and len(code) == 3, (
            f"code {code!r} not in R\\d\\d format"
        )


def test_index_by_code_round_trip():
    for r in REJECTION_REASONS:
        assert REJECTION_REASONS_BY_CODE[r.code] is r


@pytest.mark.parametrize("category", list(RejectionCategory))
def test_every_category_has_at_least_one_reason(category: RejectionCategory):
    assert reasons_for_category(category), f"no reasons for {category}"


def test_severity_to_action_covers_every_severity():
    for sev in Severity:
        assert sev in SEVERITY_TO_ACTION
    assert SEVERITY_TO_ACTION[Severity.CRITICAL] is VerdictAction.BLOCK
    assert SEVERITY_TO_ACTION[Severity.HIGH] is VerdictAction.REVIEW
    assert SEVERITY_TO_ACTION[Severity.MEDIUM] is VerdictAction.COACH


def test_each_reason_maps_to_known_standard():
    for r in REJECTION_REASONS:
        assert 1 <= r.standard <= 8, f"{r.code} standard out of range: {r.standard}"


def test_8_watt_standards_exist():
    assert set(WATT_STANDARDS.keys()) == set(range(1, 9))


def test_reasons_for_standard_returns_only_that_standard():
    for n in range(1, 9):
        for r in reasons_for_standard(n):
            assert r.standard == n


def test_canonical_call_types_match_user_data_folders():
    """User customer folders contain: Lead Gen, Passover, Verbal,
    LOA, C call, Amendment, Full call. The CallType enum must cover
    all of those plus the legacy `standalone_loa` alias and the
    distinct `closer` deal-lifecycle phase."""
    expected = {
        "lead_gen", "passover", "closer", "verbal", "loa",
        "standalone_loa", "c_call", "amendment", "full",
    }
    assert {c.value for c in CallType} == expected


def test_six_in_scope_suppliers():
    assert {s.value for s in Supplier} == {
        "bgl", "british_gas", "edf", "eon_next", "pozitive", "scottish_power",
    }
    # Every supplier has a human label.
    for s in Supplier:
        assert s in SUPPLIER_LABELS and SUPPLIER_LABELS[s]


def test_critical_reasons_default_severity():
    """A handful of critical-by-default reasons that absolutely must
    stay critical — surfacing here so a future PR can't silently
    downgrade them."""
    must_be_critical = {
        "R01",  # identity failure
        "R04",  # vulnerable customer not handled
        "R05",  # high-pressure / coercive tactics
        "R06",  # misleading information
        "R11",  # no authority check
        "R12",  # domestic customer contracted
        "R13",  # prepayment meter
        "R18",  # agent answered for customer
        "R19",  # wrong script
    }
    for code in must_be_critical:
        r = REJECTION_REASONS_BY_CODE[code]
        assert r.default_severity is Severity.CRITICAL, (
            f"{code} should be CRITICAL by default; got {r.default_severity}"
        )


def test_rejection_reason_is_frozen_dataclass():
    r = REJECTION_REASONS[0]
    with pytest.raises(Exception):  # FrozenInstanceError is subclass of AttributeError on some Pythons
        r.title = "should not be settable"  # type: ignore[misc]


def test_4_master_categories_exact():
    assert {c.value for c in RejectionCategory} == {
        "ADMIN_ERROR", "PROCESS_FAILURE", "COMPLIANCE_ISSUE", "VERBAL_SALES_ERROR",
    }


def test_reason_descriptions_not_empty():
    for r in REJECTION_REASONS:
        assert r.title, f"{r.code} has empty title"
        assert r.description, f"{r.code} has empty description"
