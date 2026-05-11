"""Tests for L3 deal lifecycle state machine + verdict aggregator.

These tests use lightweight Pydantic-style stand-ins for Deal/Call so
they exercise the pure functions in deal_lifecycle.py without needing
the L3 migration (lifecycle_status column) to be applied. The
aggregator test that needs DB access skips when the schema is not
ready — main session lands the migration later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

import pytest

from app.deal_lifecycle import (
    SUPPLIER_PHASE_MATRIX,
    call_type_to_phase,
    derive_lifecycle_status,
    required_phases,
)
from app.deal_verdict import _parse_score, aggregate_deal_verdict


# ---------------------------------------------------------------------------
# Test fixtures: minimal Deal/Call shape (only fields the state machine
# touches). Using @dataclass keeps the tests independent of the SQL
# schema until the L3 migration ships.
# ---------------------------------------------------------------------------


@dataclass
class _FakeDeal:
    supplier: Optional[str] = None
    lifecycle_status: Optional[str] = None


@dataclass
class _FakeCall:
    call_type: Optional[str]
    completed_at: Optional[datetime] = None
    score: Optional[str] = None


def _now() -> datetime:
    return datetime.utcnow()


# ---------------------------------------------------------------------------
# State-machine tests
# ---------------------------------------------------------------------------


def test_open_to_lead_gen_done():
    """Lead Gen call finalising on a fresh deal → lead_gen_done."""
    deal = _FakeDeal(supplier="E.ON")
    calls = [_FakeCall(call_type="lead_gen", completed_at=_now())]
    assert derive_lifecycle_status(deal, calls) == "lead_gen_done"


def test_eon_closer_after_lead_gen_verifies():
    """E.ON requires lead_gen + passover + closer (LOA bundled into
    Closer). All three present → verified."""
    deal = _FakeDeal(supplier="E.ON")
    calls = [
        _FakeCall(call_type="lead_gen", completed_at=_now()),
        _FakeCall(call_type="passover", completed_at=_now()),
        _FakeCall(call_type="closer", completed_at=_now()),
    ]
    assert derive_lifecycle_status(deal, calls) == "verified"


def test_british_gas_closer_stays_pending_loa():
    """British Gas closer without standalone LOA → closer_done
    (LOA still missing, NOT verified)."""
    deal = _FakeDeal(supplier="British Gas")
    calls = [
        _FakeCall(call_type="lead_gen", completed_at=_now()),
        _FakeCall(call_type="passover", completed_at=_now()),
        _FakeCall(call_type="closer", completed_at=_now()),
    ]
    assert derive_lifecycle_status(deal, calls) == "closer_done"


def test_british_gas_full_set_verifies():
    """British Gas with all four phases → verified."""
    deal = _FakeDeal(supplier="British Gas")
    calls = [
        _FakeCall(call_type="lead_gen", completed_at=_now()),
        _FakeCall(call_type="passover", completed_at=_now()),
        _FakeCall(call_type="closer", completed_at=_now()),
        _FakeCall(call_type="standalone_loa", completed_at=_now()),
    ]
    assert derive_lifecycle_status(deal, calls) == "verified"


def test_rejected_is_terminal():
    """Once a deal is rejected, no amount of new calls can move it."""
    deal = _FakeDeal(supplier="E.ON", lifecycle_status="rejected")
    calls = [
        _FakeCall(call_type="lead_gen", completed_at=_now()),
        _FakeCall(call_type="closer", completed_at=_now()),
    ]
    assert derive_lifecycle_status(deal, calls) == "rejected"


def test_c_call_is_corrective_not_required():
    """C-call is corrective: it transitions the lifecycle to c_call_done
    but doesn't appear in the supplier's required_phases list."""
    # E.ON required = lead_gen + passover + closer; c_call is NOT in that list.
    assert "c_call" not in SUPPLIER_PHASE_MATRIX["E.ON"]
    assert "c_call" not in required_phases("British Gas")

    deal = _FakeDeal(supplier="E.ON")
    calls = [
        _FakeCall(call_type="lead_gen", completed_at=_now()),
        _FakeCall(call_type="passover", completed_at=_now()),
        _FakeCall(call_type="closer", completed_at=_now()),
        _FakeCall(call_type="c_call", completed_at=_now()),
    ]
    # Required phases are still satisfied → verified path with c_call
    # overlay → c_call_done (corrective transition).
    assert derive_lifecycle_status(deal, calls) == "c_call_done"


def test_call_type_to_phase_normalises_loa_alias():
    assert call_type_to_phase("loa") == "standalone_loa"
    assert call_type_to_phase("standalone_loa") == "standalone_loa"
    assert call_type_to_phase("c_call") == "c_call"
    assert call_type_to_phase(None) is None
    assert call_type_to_phase("garbage") is None


# ---------------------------------------------------------------------------
# Verdict aggregator tests (require DB; skip if Call/CustomerDeal schema
# not aligned with this branch yet).
# ---------------------------------------------------------------------------


def test_parse_score_helpers():
    assert _parse_score("5/7") == pytest.approx(5 / 7)
    assert _parse_score("0/4") == 0.0
    assert _parse_score("4/4") == 1.0
    assert _parse_score(None) is None
    assert _parse_score("garbage") is None
    assert _parse_score("3/0") is None


def test_aggregate_verdict_weighted_score_and_missing_calls():
    """Composite score = weighted average per WEIGHTS table; missing
    calls = required minus completed phases.

    Skips cleanly if the test DB doesn't have the columns this test
    needs (deal_id, call_type, score). The main session lands the
    L3 migration; until then, this test is a no-op."""
    pytest.importorskip("sqlalchemy")
    from app.database import SessionLocal
    from app.models import Call, CustomerDeal

    db = SessionLocal()
    try:
        # Probe schema — bail if migration hasn't landed yet.
        try:
            db.query(Call.deal_id, Call.call_type, Call.score).limit(1).all()
        except Exception:
            pytest.skip("L3 schema not yet applied")

        deal = CustomerDeal(customer_name="Verdict Tester", supplier="British Gas")
        db.add(deal)
        db.flush()

        # Lead Gen 6/10 (60%, weight 0.30) + Closer 8/10 (80%, weight 0.50)
        # = (0.60*0.30 + 0.80*0.50) / 0.80
        # = (0.18 + 0.40) / 0.80
        # = 0.58 / 0.80
        # = 0.725 → 72.50
        # (The earlier 68.75 expectation was an arithmetic typo — 0.60*0.30
        # is 0.18, not 0.15. Aggregator math is correct per the L3 contract.)
        c1 = Call(
            filename="lg.wav",
            file_path="/tmp/lg.wav",
            deal_id=deal.id,
            call_type="lead_gen",
            score="6/10",
            completed_at=_now(),
        )
        c2 = Call(
            filename="cl.wav",
            file_path="/tmp/cl.wav",
            deal_id=deal.id,
            call_type="closer",
            score="8/10",
            completed_at=_now(),
        )
        db.add_all([c1, c2])
        db.commit()

        verdict = aggregate_deal_verdict(deal.id, db)
        assert verdict.composite_score == pytest.approx(72.50, abs=0.01)
        # British Gas requires standalone_loa; not yet uploaded.
        assert "standalone_loa" in verdict.missing_calls
        # lead_gen + closer satisfied.
        assert "lead_gen" not in verdict.missing_calls
        assert "closer" not in verdict.missing_calls
        assert verdict.lifecycle_status == "closer_done"
        assert len(verdict.call_breakdown) == 2
    finally:
        db.close()
