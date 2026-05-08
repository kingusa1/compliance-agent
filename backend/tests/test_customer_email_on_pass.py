"""Sprint A2 (v3-watt-coverage W5) — verify a PASS verdict auto-fires
``send_customer_email_for_call`` and a FAIL verdict does not.

Setup mirrors test_rejections.py — in-memory SQLite + StaticPool, autouse
clean_db fixture overrides ``get_db``. The customer email helper is
patched so we assert the call without spinning up the template
machinery.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Call, Profile


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


def _seed_call() -> str:
    """Seed a call with one checkpoint we can submit a verdict on."""
    db = TestSessionLocal()
    try:
        cps = [{
            "id": "cp_1",
            "name": "Confirm consent",
            "status": "pass",
            "verdict": "pass",
            "confidence": 0.9,
            "rule_id": "MISSING_PRICE",
        }]
        c = Call(
            id="c-a2-1",
            filename="t.mp3",
            file_path="c-a2-1/t.mp3",
            duration_seconds=10.0,
            transcript="...",
            detected_supplier="E.ON Next Energy",
            agent_name="Sammie",
            checkpoint_results=json.dumps(cps),
        )
        db.add(c)
        db.commit()
    finally:
        db.close()
    return "c-a2-1"


def test_pass_verdict_triggers_customer_email(mock_jwks, seed_profiles_local, auth):
    """W5/A2 — PASS verdict on a checkpoint triggers
    ``send_customer_email_for_call`` with the correct call_id.

    We patch the helper at its source module (``app.email_routes``) — the
    PASS branch in hitl_routes does a fresh ``from app.email_routes import
    send_customer_email_for_call`` each call, so the patch is picked up.
    """
    cid = _seed_call()
    with patch("app.email_routes.send_customer_email_for_call") as send:
        send.return_value = {
            "sent": True, "message_id": "msg_test", "preview_html": "",
            "to": None, "cc": [], "missing_fields": [],
        }
        r = client.post(
            f"/api/calls/{cid}/verdict",
            headers=auth("sarah"),
            json={
                "checkpoint_id": "cp_1",
                "verdict": "PASS",
                "reasoning": "all good",
            },
        )
    assert r.status_code == 200, r.text
    assert send.called, "send_customer_email_for_call should be called on PASS"
    # call_id is passed as a kwarg.
    _, kwargs = send.call_args
    assert kwargs.get("call_id") == cid


def test_fail_verdict_does_not_trigger_customer_email(mock_jwks, seed_profiles_local, auth):
    """W5/A2 — FAIL verdict creates a rejection but must NOT fire the
    customer-confirmation email (the customer didn't get a clean contract)."""
    cid = _seed_call()
    with patch("app.email_routes.send_customer_email_for_call") as send:
        r = client.post(
            f"/api/calls/{cid}/verdict",
            headers=auth("sarah"),
            json={
                "checkpoint_id": "cp_1",
                "verdict": "FAIL",
                "reasoning": "agent skipped recording disclosure",
            },
        )
    assert r.status_code == 200, r.text
    assert not send.called, "FAIL verdict must not trigger customer email"


def test_pass_verdict_email_failure_does_not_break_verdict(mock_jwks, seed_profiles_local, auth):
    """A2 best-effort guarantee — if the email helper raises, the verdict
    submit still returns 200 (the reviewer's audit row is what matters)."""
    cid = _seed_call()
    with patch(
        "app.email_routes.send_customer_email_for_call",
        side_effect=RuntimeError("smtp explode"),
    ):
        r = client.post(
            f"/api/calls/{cid}/verdict",
            headers=auth("sarah"),
            json={
                "checkpoint_id": "cp_1",
                "verdict": "PASS",
                "reasoning": "ok",
            },
        )
    assert r.status_code == 200, r.text
    assert r.json()["saved"] is True
