"""B-3 — POST /api/customers create-customer endpoint.

Three tests:
  1. Valid payload returns 201 + slug derived from legal_name (+ trading_as).
  2. Idempotent on slug — re-POSTing the same legal_name returns the
     existing row's uuid (no duplicate).
  3. Missing legal_name → 422 (Pydantic validation).
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

# Pre-import models so SQLAlchemy registers the customers table on
# Base.metadata before any test fixture call_create_all (mirrors the
# pattern in test_intake.py).
from app import models as _models  # noqa: F401
from app.auth import current_user
from app.main import app
from app.reviewers import current_reviewer, require_lead

# 2026-05-24 — `POST /api/customers` now requires `require_lead`. Tests
# stub the JWT dependency so they keep exercising the create logic
# without needing a live Supabase Auth round-trip. The pop on teardown
# matches the per-file fixture pattern used by test_audit_coverage.py
# so the override doesn't leak into other test files that explicitly
# verify the unauthenticated 401 path.
_STUB_LEAD = {
    "id": "test-lead",
    "email": "lead@compliance-agent.local",
    "name": "Test Lead",
    "role": "lead",
}


@pytest.fixture(autouse=True)
def _stub_auth():
    app.dependency_overrides[current_user] = lambda: _STUB_LEAD
    app.dependency_overrides[current_reviewer] = lambda: _STUB_LEAD
    app.dependency_overrides[require_lead] = lambda: _STUB_LEAD
    # `record_audit` writes `actor_id` which FK's to profiles.id; seed
    # the test-lead profile so the chain extension doesn't 23503.
    from app.database import SessionLocal
    from app.models import Profile
    db = SessionLocal()
    try:
        if not db.query(Profile).filter_by(id="test-lead").first():
            db.add(Profile(
                id="test-lead",
                email="lead@compliance-agent.local",
                name="Test Lead",
                role="lead",
                active=True,
            ))
            db.commit()
    finally:
        db.close()
    yield
    app.dependency_overrides.pop(current_user, None)
    app.dependency_overrides.pop(current_reviewer, None)
    app.dependency_overrides.pop(require_lead, None)


client = TestClient(app)


def _unique_name(prefix: str = "Acme Co") -> str:
    """Suffix the legal name with a uuid fragment so reruns against the
    shared Postgres test DB don't collide on the slug uniqueness index."""
    return f"{prefix} {uuid.uuid4().hex[:8]}"


def test_create_customer_valid_returns_201_and_slug():
    legal_name = _unique_name("Acme Holdings")
    r = client.post(
        "/api/customers",
        json={
            "legal_name": legal_name,
            "trading_as": "Acme",
            "address_postcode": "SW1A 1AA",
            "business_type": "limited",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["customer"]["legal_name"] == legal_name
    assert body["customer"]["trading_as"] == "Acme"
    assert body["slug"] == body["customer"]["slug"]
    # Slug is lowercase + hyphenated.
    assert body["slug"] == body["slug"].lower()
    assert " " not in body["slug"]
    assert body["customer"]["id"]


def test_create_customer_dedupes_on_slug():
    legal_name = _unique_name("Dupe Test")
    r1 = client.post("/api/customers", json={"legal_name": legal_name})
    assert r1.status_code == 201, r1.text
    first = r1.json()

    r2 = client.post("/api/customers", json={"legal_name": legal_name})
    assert r2.status_code == 201, r2.text
    second = r2.json()

    assert first["slug"] == second["slug"]
    assert first["customer"]["id"] == second["customer"]["id"]


def test_create_customer_missing_legal_name_returns_422():
    r = client.post("/api/customers", json={"trading_as": "Just trading"})
    assert r.status_code == 422, r.text
