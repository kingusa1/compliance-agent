"""A5: pipeline finalize auto-creates a Rejection row when score < threshold.

Mirrors the spec in `.planning/v3-rebuild/2026-05-04-tracker-plan.md` task A5.
Uses the existing SQLite-backed `test_db` fixture (aliased to `db_session` to
match the plan's parameter name — same pattern as test_business_detect.py).
The `build_rejection_for_call` LLM call is mocked so tests stay hermetic.
"""
import json
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.orm import Session

from app.models import Call, Customer, CustomerDeal, Rejection


@pytest.fixture
def db_session(test_db) -> Session:
    return test_db


@pytest.fixture
def sample_call_with_failing_cps(db_session):
    cust = Customer(legal_name="Evangelical Church", slug="evangelical-church")
    db_session.add(cust)
    db_session.flush()
    deal = CustomerDeal(
        customer_id=cust.id,
        customer_name="Evangelical Church",
        supplier="E.ON Next",
        status="in_progress",
    )
    db_session.add(deal)
    db_session.flush()
    call = Call(
        filename="church.mp3",
        file_path="/tmp/church.mp3",
        deal_id=deal.id,
        detected_supplier="E.ON Next",
        agent_name="Afaq",
        score="10/24",
        checkpoint_results=json.dumps([
            {
                "name": "Pricing Disclosure",
                "status": "fail",
                "evidence": "agent stated VAT included",
                "notes": "incorrect",
            },
        ]),
        status="processing",
    )
    db_session.add(call)
    db_session.commit()
    return call


@pytest.fixture
def sample_high_score_call(db_session):
    call = Call(
        filename="ok.mp3",
        file_path="/tmp/ok.mp3",
        score="20/24",
        status="processing",
        checkpoint_results="[]",
    )
    db_session.add(call)
    db_session.commit()
    return call


@pytest.mark.asyncio
async def test_finalize_creates_rejection_when_below_threshold(
    db_session, sample_call_with_failing_cps
):
    call = sample_call_with_failing_cps  # score=10/24 → ratio 0.417 < 0.7
    with patch(
        "app.pipeline.build_rejection_for_call", new_callable=AsyncMock
    ) as mb:
        mb.return_value = {
            "call_id": str(call.id),
            "customer_slug": "evangelical-church",
            "supplier": "E.ON Next",
            "sales_agent": "Afaq",
            "category": "COMPLIANCE_ISSUE",
            "rejection_reason": "Stated VAT incorrectly",
            "fix_required": "Re-quote rates",
            "status": "NOT_STARTED",
        }
        from app.pipeline import _maybe_create_rejection
        await _maybe_create_rejection(call, db_session)
    db_session.commit()
    rej = db_session.query(Rejection).filter_by(call_id=call.id).first()
    assert rej is not None
    assert rej.category == "COMPLIANCE_ISSUE"
    assert rej.rejection_reason == "Stated VAT incorrectly"
    assert rej.status == "NOT_STARTED"


@pytest.mark.asyncio
async def test_finalize_skips_rejection_when_above_threshold(
    db_session, sample_high_score_call
):
    call = sample_high_score_call  # 20/24 = 0.833 ≥ 0.7
    from app.pipeline import _maybe_create_rejection
    await _maybe_create_rejection(call, db_session)
    db_session.commit()
    assert db_session.query(Rejection).filter_by(call_id=call.id).first() is None


@pytest.mark.asyncio
async def test_finalize_is_idempotent(db_session, sample_call_with_failing_cps):
    """Running the helper twice on the same call must NOT produce a 2nd row."""
    call = sample_call_with_failing_cps
    with patch(
        "app.pipeline.build_rejection_for_call", new_callable=AsyncMock
    ) as mb:
        mb.return_value = {
            "call_id": str(call.id),
            "customer_slug": "evangelical-church",
            "supplier": "E.ON Next",
            "sales_agent": "Afaq",
            "category": "COMPLIANCE_ISSUE",
            "rejection_reason": "x",
            "fix_required": "y",
            "status": "NOT_STARTED",
        }
        from app.pipeline import _maybe_create_rejection
        await _maybe_create_rejection(call, db_session)
        db_session.commit()
        await _maybe_create_rejection(call, db_session)
        db_session.commit()
    rows = db_session.query(Rejection).filter_by(call_id=call.id).all()
    assert len(rows) == 1
