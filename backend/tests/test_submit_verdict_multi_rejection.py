"""Tracker Task 3 — when a reviewer commits a FAIL verdict, one Rejection
row is auto-created for EVERY FAIL/PARTIAL CallCheckpoint on the call (not
just the rule_id the verdict referenced). Mirrors the Watt XLSX where one
bad call produces N rejection rows (one per failed line).

PASS path is a sanity guard: zero rejections + ``call.compliant = True``.

Setup mirrors test_customer_email_on_pass.py — in-memory SQLite + StaticPool,
autouse clean_db fixture overrides ``get_db``. The customer-email helper is
patched on the PASS test so we don't reach the email machinery.
"""
from __future__ import annotations

import json
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import (
    Call,
    CallCheckpoint,
    Customer,
    CustomerDeal,
    Profile,
    Rejection,
)


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


client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_db():
    app.dependency_overrides[get_db] = _override_get_db
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)
    yield


@pytest.fixture
def seed_profiles_local():
    db = TestSessionLocal()
    try:
        db.add_all([
            Profile(id="sarah", email="sarah@test.local", name="Sarah Ali",  role="reviewer", active=True),
            Profile(id="omar",  email="omar@test.local",  name="Omar Hassan", role="lead",     active=True),
        ])
        db.commit()
    finally:
        db.close()


def test_fail_verdict_creates_one_rejection_per_failed_checkpoint(
    mock_jwks, seed_profiles_local, auth
):
    """FAIL verdict loops the call's CallCheckpoint rows where passed=False
    and emits one Rejection per failure — uses the ``ai_category`` column
    on each row (W4.7 high-confidence path) so categories differ per line."""
    cust_id = uuid.uuid4()
    deal_id = uuid.uuid4()
    call_id = "c-tr3-fail-" + uuid.uuid4().hex[:8]

    # The submit_verdict endpoint requires a valid checkpoint_id pointing
    # into call.checkpoint_results — we use synthetic "cp_0" addressing
    # (index 0 of the JSON array). The new loop reads CallCheckpoint rows
    # for the per-failure rejections, so the JSON only needs to satisfy
    # the resolver, not the loop.
    cps_json = [
        {"id": "cp_0", "name": "Recording Disclosure", "status": "fail",
         "verdict": "fail", "rule_id": "RECORDING_DISCLOSURE"},
        {"name": "Capacity Charges", "status": "fail"},
        {"name": "Marketing Consent", "status": "partial"},
        {"name": "Final Confirmation", "status": "pass"},
    ]

    db = TestSessionLocal()
    try:
        cust = Customer(id=cust_id, legal_name="Acme", slug="acme")
        deal = CustomerDeal(
            id=deal_id, customer_id=cust_id, customer_name="Acme",
            supplier="E.ON Next", status="in_progress",
        )
        call = Call(
            id=call_id, filename="t.mp3", file_path="t/t.mp3",
            deal_id=deal_id, status="completed", score="20/24",
            detected_supplier="E.ON Next",
            checkpoint_results=json.dumps(cps_json),
        )
        cps = [
            CallCheckpoint(
                id=str(uuid.uuid4()), call_id=call_id,
                rule_text="Recording Disclosure", passed=False,
                ai_category="COMPLIANCE_ERROR",
                ai_fix_required="AMENDMENT_CALL",
                ai_category_confidence=0.85,
                ai_rejection_reason="Agent did not disclose recording",
                ai_narrative_notes="The opening did not contain the recording-disclosure script line.",
            ),
            CallCheckpoint(
                id=str(uuid.uuid4()), call_id=call_id,
                rule_text="Capacity Charges", passed=False,
                ai_category="VERBAL_SALES_ERROR",
                ai_fix_required="AMENDMENT_CALL",
                ai_category_confidence=0.80,
                ai_rejection_reason="ASC charges not stated",
                ai_narrative_notes="Agent stated unit rate but not ASC.",
            ),
            CallCheckpoint(
                id=str(uuid.uuid4()), call_id=call_id,
                rule_text="Marketing Consent", passed=False,
                ai_category="COMPLIANCE_ISSUE",
                ai_fix_required="CONFIRMATION_CALL",
                ai_category_confidence=0.75,
                ai_rejection_reason="Marketing consent unclear",
                ai_narrative_notes="Yes/no answer was inaudible.",
            ),
            CallCheckpoint(
                id=str(uuid.uuid4()), call_id=call_id,
                rule_text="Final Confirmation", passed=True,
            ),
        ]
        db.add_all([cust, deal, call] + cps)
        db.commit()
    finally:
        db.close()

    r = client.post(
        f"/api/calls/{call_id}/verdict",
        headers=auth("sarah"),
        json={
            "checkpoint_id": "cp_0",
            "verdict": "FAIL",
            "reasoning": "Multiple failures",
        },
    )
    assert r.status_code == 200, r.text

    db = TestSessionLocal()
    try:
        rejs = db.query(Rejection).filter(Rejection.call_id == call_id).all()
        assert len(rejs) == 3, (
            f"expected 3 rejections (1 per FAIL/PARTIAL checkpoint), got {len(rejs)}"
        )
        cats = sorted([rej.category for rej in rejs])
        assert cats == ["COMPLIANCE_ERROR", "COMPLIANCE_ISSUE", "VERBAL_SALES_ERROR"]
    finally:
        db.close()


def test_pass_verdict_creates_zero_rejections(mock_jwks, seed_profiles_local, auth):
    """PASS verdict on a call with no failed checkpoints creates no
    Rejection rows AND flips ``call.compliant = True``."""
    call_id = "c-tr3-pass-" + uuid.uuid4().hex[:8]

    # Single PASS checkpoint so the resolver finds cp_0; no CallCheckpoint
    # rows with passed=False so the FAIL-multi loop has nothing to do.
    cps_json = [{
        "id": "cp_0",
        "name": "Final Confirmation",
        "status": "pass",
        "verdict": "pass",
    }]

    db = TestSessionLocal()
    try:
        call = Call(
            id=call_id, filename="p.mp3", file_path="p/p.mp3",
            status="completed", score="24/24",
            detected_supplier="E.ON Next",
            checkpoint_results=json.dumps(cps_json),
        )
        db.add(call)
        db.commit()
    finally:
        db.close()

    # Patch the customer-email helper — Sprint A2 fires it on PASS, but we
    # don't want to exercise the template machinery here.
    with patch("app.email_routes.send_customer_email_for_call") as send:
        send.return_value = {
            "sent": True, "message_id": "msg_test", "preview_html": "",
            "to": None, "cc": [], "missing_fields": [],
        }
        r = client.post(
            f"/api/calls/{call_id}/verdict",
            headers=auth("sarah"),
            json={
                "checkpoint_id": "cp_0",
                "verdict": "PASS",
                "reasoning": "All checkpoints clean",
            },
        )
    assert r.status_code == 200, r.text

    db = TestSessionLocal()
    try:
        assert db.query(Rejection).filter(Rejection.call_id == call_id).count() == 0
        call = db.query(Call).filter_by(id=call_id).one()
        assert call.compliant is True, (
            f"PASS verdict must flip call.compliant=True, got {call.compliant!r}"
        )
    finally:
        db.close()
