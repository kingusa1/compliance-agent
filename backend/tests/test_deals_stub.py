"""Verify POST /api/deals/stub creates a CustomerDeal in 'pending_audio' state.

A6 (Tracker XLSX Parity, Phase A): same-deal upload mode helper. UI calls
this once before firing N parallel /api/calls/upload requests with the
returned deal_id, so all N audio files attach to one deal record. Pipeline
detect_metadata is race-safe (only-fill-if-blank) so the first call's
detection backfills the shared stub deal.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal
from app.models import CustomerDeal, Profile

client = TestClient(app)


@pytest.fixture(autouse=True)
def _install_auth_stub():
    """2026-05-24 wiring audit C3 added Depends(current_reviewer) to
    POST /api/deals/stub + stamps user["id"] into audit_log.actor_id
    (was a client-controlled x-user-id header). Tests must:
      1. Override current_user so the request authenticates as admin
      2. Seed a Profile row matching the stub id so audit_log's
         actor_id FK doesn't violate.
    """
    from app.auth import current_user, require_lead
    from app.reviewers import current_reviewer

    _stub_admin = {
        "id": "test-admin",
        "email": "test-admin@compliance-agent.local",
        "name": "Test Admin",
        "role": "admin",
    }
    app.dependency_overrides[current_user] = lambda: _stub_admin
    app.dependency_overrides[current_reviewer] = lambda: _stub_admin
    app.dependency_overrides[require_lead] = lambda: _stub_admin

    db = SessionLocal()
    try:
        if not db.query(Profile).filter_by(id="test-admin").first():
            db.add(Profile(
                id="test-admin",
                email="test-admin@compliance-agent.local",
                name="Test Admin",
                role="admin",
                active=True,
            ))
            db.commit()
    finally:
        db.close()
    yield


def test_post_deals_stub_returns_uuid():
    r = client.post("/api/deals/stub")
    assert r.status_code == 200, r.text
    data = r.json()
    assert "deal_id" in data

    # Returned id must be a parseable UUID string.
    deal_uuid = uuid.UUID(data["deal_id"])

    db = SessionLocal()
    try:
        deal = db.query(CustomerDeal).filter_by(id=deal_uuid).first()
        assert deal is not None, f"deal {deal_uuid} not persisted"
        assert deal.status == "pending_audio"
        # Stub marker the pipeline + UI use to recognise an unfilled deal.
        assert deal.customer_name == "(pending audio upload)"
    finally:
        db.close()
