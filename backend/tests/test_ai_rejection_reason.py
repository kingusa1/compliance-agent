"""Sprint A1 (v3-watt-coverage W5) — verify Claude's ``ai_rejection_reason``
+ ``ai_narrative_notes`` flow into the auto-created Rejection row.

Setup mirrors test_ai_category_suggestion.py — in-memory SQLite + StaticPool,
autouse clean_db fixture overrides ``get_db``. We unit-test the decision
logic in ``auto_create_rejection_for_verdict`` directly (no HTTP layer
needed) so the assertion targets are crisp.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Call, Customer, CustomerDeal, Rejection
from app.rejections_routes import auto_create_rejection_for_verdict


_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Base.metadata.create_all(_engine)
TestSessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)


def _override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def clean_db():
    app.dependency_overrides[get_db] = _override_get_db
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)
    yield


def _seed(db) -> Call:
    customer = Customer(
        id=uuid.uuid4(),
        legal_name="Acme Ltd",
        slug=f"acme-{uuid.uuid4().hex[:6]}",
    )
    deal = CustomerDeal(
        id=uuid.uuid4(),
        customer_id=customer.id,
        customer_name="Acme Ltd",
        status="in_progress",
        supplier="E.ON Next",
    )
    call = Call(
        id="c-a1-" + uuid.uuid4().hex[:8],
        filename="t.mp3",
        file_path="t/t.mp3",
        deal_id=deal.id,
        status="completed",
        detected_supplier="E.ON Next",
        agent_name="Sammie",
    )
    db.add_all([customer, deal, call])
    db.commit()
    db.refresh(call)
    return call


def test_ai_rejection_reason_propagates_to_rejection_row():
    """W5/A1 happy path — analyzer-supplied ai_rejection_reason +
    ai_narrative_notes land on Rejection.rejection_reason +
    Rejection.fix_narrative.

    2026-05-15 audit: AI narrative is written to ``fix_narrative`` (not
    ``outcome_narrative``) so the reviewer's outcome-notes slot stays
    clean for human input. See rejections_routes.py:1020 area for the
    write site.
    """
    db = TestSessionLocal()
    try:
        call = _seed(db)
        checkpoint = {
            "name": "Recording Disclosure",
            "rule_id": "RECORDING_DISCLOSURE",
            "ai_category": "COMPLIANCE_ERROR",
            "ai_fix_required": "AMENDMENT_CALL",
            "ai_category_confidence": 0.85,
            "ai_rejection_reason": "Agent did not state the call was being recorded",
            "ai_narrative_notes": (
                "The opening lines do not contain the recording-disclosure "
                "script line. An amendment call is required to satisfy the "
                "disclosure requirement before the contract goes live."
            ),
        }
        rej = auto_create_rejection_for_verdict(
            db,
            call=call,
            actor_id="test-user",
            verdict_action="FAIL",
            reason="manual reviewer reason",
            rule_id="RECORDING_DISCLOSURE",
            checkpoint=checkpoint,
        )
        db.commit()

        assert rej is not None
        assert rej.rejection_reason == "Agent did not state the call was being recorded"
        # AI narrative writes to fix_narrative (reviewer's outcome_narrative
        # slot stays clean).
        assert rej.fix_narrative == checkpoint["ai_narrative_notes"]
        assert rej.outcome_narrative is None
        # AI confidence ≥ 0.7, so the AI-suggested category + fix should win.
        assert rej.category == "COMPLIANCE_ERROR"
        assert rej.fix_required == "AMENDMENT_CALL"
    finally:
        db.close()


def test_falls_back_to_manual_reason_when_ai_missing():
    """When the analyzer didn't supply ai_rejection_reason (e.g. legacy
    call, batch error), the manual reviewer reason still wins. outcome_
    narrative remains None — we don't fabricate coaching text."""
    db = TestSessionLocal()
    try:
        call = _seed(db)
        rej = auto_create_rejection_for_verdict(
            db,
            call=call,
            actor_id="test-user",
            verdict_action="FAIL",
            reason="reviewer typed this directly",
            rule_id="RECORDING_DISCLOSURE",
            checkpoint=None,
        )
        db.commit()

        assert rej is not None
        assert rej.rejection_reason == "reviewer typed this directly"
        assert rej.outcome_narrative is None
    finally:
        db.close()


def test_falls_back_when_ai_reason_is_blank_string():
    """A blank-string ai_rejection_reason is treated as missing — sanitize
    so we never write an empty-string headline onto the rejection row."""
    db = TestSessionLocal()
    try:
        call = _seed(db)
        checkpoint = {
            "name": "VAT clause",
            "ai_rejection_reason": "   ",
            "ai_narrative_notes": "",
        }
        rej = auto_create_rejection_for_verdict(
            db,
            call=call,
            actor_id="test-user",
            verdict_action="FAIL",
            reason="agent skipped VAT",
            rule_id="VAT_EXCLUSION",
            checkpoint=checkpoint,
        )
        db.commit()

        assert rej is not None
        assert rej.rejection_reason == "agent skipped VAT"
        assert rej.outcome_narrative is None
    finally:
        db.close()
