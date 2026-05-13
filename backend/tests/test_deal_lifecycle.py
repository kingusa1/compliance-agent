"""Deal lifecycle state-machine tests — 2026-05-12 taxonomy rebuild.

These exercise the pure functions in ``app.deal_lifecycle`` after the
4-stage lockdown (``lead_gen / pre_sales / verbal / loa``) and the
"latest call per phase wins" rule.

The aggregator integration test that needs DB access (test_aggregate_…)
skips cleanly when the L3 schema isn't applied locally — the latest-wins
contract is verified against the pure functions instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from app.deal_lifecycle import (
    SUPPLIER_PHASE_MATRIX,
    call_type_to_phase,
    derive_lifecycle_status,
    required_phases,
)


@dataclass
class _FakeDeal:
    supplier: Optional[str] = None
    lifecycle_status: Optional[str] = None


@dataclass
class _FakeCall:
    call_type: Optional[str]
    completed_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    score: Optional[str] = None
    compliance_status: Optional[str] = None
    compliant: Optional[bool] = None


def _now() -> datetime:
    return datetime.utcnow()


def _earlier(minutes: int = 30) -> datetime:
    return datetime.utcnow() - timedelta(minutes=minutes)


# ---------------------------------------------------------------------------
# Taxonomy + matrix sanity
# ---------------------------------------------------------------------------


def test_matrix_uses_only_canonical_4_stages():
    """The phase matrix must not surface any legacy phase name."""
    legacy = {"passover", "closer", "standalone_loa", "c_call", "amendment", "full"}
    for supplier, phases in SUPPLIER_PHASE_MATRIX.items():
        assert legacy.isdisjoint(phases), (
            f"{supplier} still references legacy phases: {phases}"
        )


def test_required_phases_eon_and_non_eon_are_three_stages():
    """E.ON and non-E.ON both expose 3 audio phases. LOA is bundled in
    verbal for E.ON; LOA is paper-only for non-E.ON."""
    assert required_phases("E.ON") == ["lead_gen", "pre_sales", "verbal"]
    assert required_phases("E.ON Next") == ["lead_gen", "pre_sales", "verbal"]
    assert required_phases("British Gas") == ["lead_gen", "pre_sales", "verbal"]
    assert required_phases("Pozitive") == ["lead_gen", "pre_sales", "verbal"]
    # Unknown supplier defaults to non-E.ON 3-phase variant.
    assert required_phases("MysterySupplier") == ["lead_gen", "pre_sales", "verbal"]
    assert required_phases(None) == ["lead_gen", "pre_sales", "verbal"]


def test_call_type_to_phase_locked_to_four_values():
    assert call_type_to_phase("lead_gen") == "lead_gen"
    assert call_type_to_phase("pre_sales") == "pre_sales"
    assert call_type_to_phase("verbal") == "verbal"
    assert call_type_to_phase("loa") == "loa"
    # Old vocabulary is gone — must NOT map.
    assert call_type_to_phase("closer") is None
    assert call_type_to_phase("passover") is None
    assert call_type_to_phase("standalone_loa") is None
    assert call_type_to_phase("c_call") is None
    assert call_type_to_phase("amendment") is None
    assert call_type_to_phase("full") is None
    assert call_type_to_phase(None) is None


# ---------------------------------------------------------------------------
# State-machine: open → lead_gen_done → pre_sales_done → verbal_done → verified
# ---------------------------------------------------------------------------


def test_open_when_no_compliant_calls():
    deal = _FakeDeal(supplier="E.ON")
    calls = [
        _FakeCall(
            call_type="lead_gen",
            completed_at=_now(),
            created_at=_now(),
            compliance_status="non_compliant",
        ),
    ]
    assert derive_lifecycle_status(deal, calls) == "open"


def test_lead_gen_done_when_only_lead_gen_compliant():
    deal = _FakeDeal(supplier="E.ON")
    calls = [
        _FakeCall(
            call_type="lead_gen",
            completed_at=_now(),
            created_at=_now(),
            compliance_status="compliant",
        ),
    ]
    assert derive_lifecycle_status(deal, calls) == "lead_gen_done"


def test_verified_when_all_three_phases_compliant():
    deal = _FakeDeal(supplier="E.ON")
    calls = [
        _FakeCall(call_type="lead_gen", completed_at=_now(), created_at=_now(), compliance_status="compliant"),
        _FakeCall(call_type="pre_sales", completed_at=_now(), created_at=_now(), compliance_status="compliant"),
        _FakeCall(call_type="verbal", completed_at=_now(), created_at=_now(), compliance_status="compliant"),
    ]
    assert derive_lifecycle_status(deal, calls) == "verified"


def test_non_eon_verifies_without_loa_call():
    """Non-E.ON LOA is paper/DocuSign — no LOA call needed for verify."""
    deal = _FakeDeal(supplier="British Gas")
    calls = [
        _FakeCall(call_type="lead_gen", completed_at=_now(), created_at=_now(), compliance_status="compliant"),
        _FakeCall(call_type="pre_sales", completed_at=_now(), created_at=_now(), compliance_status="compliant"),
        _FakeCall(call_type="verbal", completed_at=_now(), created_at=_now(), compliance_status="compliant"),
    ]
    assert derive_lifecycle_status(deal, calls) == "verified"


# ---------------------------------------------------------------------------
# Latest-call-per-phase wins
# ---------------------------------------------------------------------------


def test_latest_compliant_overrides_earlier_failure():
    """lead_gen #1 = non_compliant, lead_gen #2 = compliant → phase done."""
    deal = _FakeDeal(supplier="E.ON")
    calls = [
        _FakeCall(
            call_type="lead_gen",
            completed_at=_earlier(60),
            created_at=_earlier(60),
            compliance_status="non_compliant",
        ),
        _FakeCall(
            call_type="lead_gen",
            completed_at=_now(),
            created_at=_now(),
            compliance_status="compliant",
        ),
    ]
    # Only lead_gen is done — other phases still pending.
    assert derive_lifecycle_status(deal, calls) == "lead_gen_done"


def test_latest_failure_after_pass_blocks_phase():
    """lead_gen #1 = compliant, lead_gen #2 = non_compliant → phase NOT done."""
    deal = _FakeDeal(supplier="E.ON")
    calls = [
        _FakeCall(
            call_type="lead_gen",
            completed_at=_earlier(60),
            created_at=_earlier(60),
            compliance_status="compliant",
        ),
        _FakeCall(
            call_type="lead_gen",
            completed_at=_now(),
            created_at=_now(),
            compliance_status="non_compliant",
        ),
    ]
    assert derive_lifecycle_status(deal, calls) == "open"


def test_legacy_compliant_bool_still_counts():
    """Calls that predate the compliance_status column should still resolve
    via the legacy ``compliant=True`` flag."""
    deal = _FakeDeal(supplier="E.ON")
    calls = [
        _FakeCall(
            call_type="lead_gen",
            completed_at=_now(),
            created_at=_now(),
            compliant=True,
        ),
    ]
    assert derive_lifecycle_status(deal, calls) == "lead_gen_done"


# ---------------------------------------------------------------------------
# Terminal states + progressive ordering
# ---------------------------------------------------------------------------


def test_rejected_is_terminal():
    deal = _FakeDeal(supplier="E.ON", lifecycle_status="rejected")
    calls = [
        _FakeCall(call_type="lead_gen", completed_at=_now(), created_at=_now(), compliance_status="compliant"),
        _FakeCall(call_type="verbal", completed_at=_now(), created_at=_now(), compliance_status="compliant"),
    ]
    assert derive_lifecycle_status(deal, calls) == "rejected"


def test_progressive_status_picks_most_advanced_phase():
    """When lead_gen + verbal compliant but pre_sales is missing, surface
    the most-advanced phase (verbal_done)."""
    deal = _FakeDeal(supplier="E.ON")
    calls = [
        _FakeCall(call_type="lead_gen", completed_at=_now(), created_at=_now(), compliance_status="compliant"),
        _FakeCall(call_type="verbal", completed_at=_now(), created_at=_now(), compliance_status="compliant"),
    ]
    assert derive_lifecycle_status(deal, calls) == "verbal_done"


def test_incomplete_call_does_not_count():
    """A call without completed_at is in-flight and never marks a phase done."""
    deal = _FakeDeal(supplier="British Gas")
    calls = [
        _FakeCall(call_type="lead_gen", completed_at=None, created_at=_now(), compliance_status="compliant"),
    ]
    assert derive_lifecycle_status(deal, calls) == "open"
