"""GET /api/observability/audit + /api/observability/failed-jobs routes."""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.audit import record_audit
from app.auth import current_user
from app.database import SessionLocal
from app.main import app
from app.workflows.redispatch_watchdog import record_failed_job

# observability_router is auth-gated (2026-05-30 security audit). Stub an authenticated
# admin so the audit/failed-jobs route tests exercise the handlers, not the 401 gate.
_STUB_USER = {"id": "test-admin", "email": "admin@test.local", "name": "Test", "role": "admin"}


@pytest.fixture
def client():
    app.dependency_overrides[current_user] = lambda: _STUB_USER
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(current_user, None)


def test_audit_route_returns_recent_rows(client):
    db = SessionLocal()
    try:
        record_audit(db, action="probe", entity_type="test",
                     entity_id="x", payload={"a": 1})
        db.commit()
    finally:
        db.close()
    r = client.get("/api/observability/audit?limit=10")
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert any(row["action"] == "probe" for row in rows)


def test_failed_jobs_route_returns_recent_rows(client):
    from app.models import Call

    db = SessionLocal()
    cid = str(uuid.uuid4())
    try:
        db.add(Call(id=cid, filename="t.mp3", file_path="/tmp/t.mp3", status="failed"))
        db.commit()
        record_failed_job(db, call_id=cid, last_step="transcribe",
                          attempts=3, last_error="x")
        db.commit()
    finally:
        db.close()
    r = client.get("/api/observability/failed-jobs?limit=10")
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert any(row["call_id"] == cid for row in rows)
