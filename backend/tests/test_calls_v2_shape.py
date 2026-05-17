"""Verify GET /api/calls/{id} returns the v2 deal-centric shape.

Uses a real call inserted via SQLAlchemy directly (fast, deterministic) instead
of the full upload pipeline.
"""
import uuid
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal
from app.models import Call, CustomerDeal
from app.reviewers import current_reviewer

client = TestClient(app)


# Audit 2026-05-16 C7: GET /api/calls/{id} now requires auth (the endpoint
# embeds a signed audio URL). Override the dep so these schema-shape tests
# assert against 200/404 instead of the auth gate's 401.
# 2026-05-18: moved from module-load to autouse fixture so the conftest
# aggressive-clear of dependency_overrides between tests doesn't strip
# this override before the next test runs.
@pytest.fixture(autouse=True)
def _override_auth():
    app.dependency_overrides[current_reviewer] = lambda: {
        "id": "test-reviewer",
        "email": "test@compliance-agent.local",
        "role": "admin",
    }
    yield


def _make_deal(customer_name: str = "ShapeCo") -> str:
    db = SessionLocal()
    try:
        d = CustomerDeal(customer_name=customer_name)
        db.add(d)
        db.commit()
        db.refresh(d)
        return str(d.id)
    finally:
        db.close()


def _make_call(deal_id: str | None = None) -> str:
    db = SessionLocal()
    try:
        name = f"shape-{uuid.uuid4()}.wav"
        c = Call(
            filename=name,
            file_path=f"/tmp/{name}",
            customer_name="ShapeCo",
            status="processing",
            deal_id=uuid.UUID(deal_id) if deal_id else None,
            call_type="full",
        )
        db.add(c)
        db.commit()
        db.refresh(c)
        return str(c.id)
    finally:
        db.close()


def test_get_call_returns_v2_shape_keys():
    deal_id = _make_deal("ShapeCo2")
    call_id = _make_call(deal_id)

    r = client.get(f"/api/calls/{call_id}")
    assert r.status_code == 200, r.text
    body = r.json()

    for key in ("id", "deal_id", "call_type", "supplier_variant", "segments", "flags"):
        assert key in body, f"missing key: {key}, got: {list(body)}"

    assert body["deal_id"] == deal_id
    assert body["call_type"] == "full"
    assert isinstance(body["segments"], list)
    assert isinstance(body["flags"], list)


def test_get_call_404_when_not_found():
    r = client.get(f"/api/calls/{uuid.uuid4()}")
    assert r.status_code == 404
