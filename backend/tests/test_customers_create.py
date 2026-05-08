"""B-3 — POST /api/customers create-customer endpoint.

Three tests:
  1. Valid payload returns 201 + slug derived from legal_name (+ trading_as).
  2. Idempotent on slug — re-POSTing the same legal_name returns the
     existing row's uuid (no duplicate).
  3. Missing legal_name → 422 (Pydantic validation).
"""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

# Pre-import models so SQLAlchemy registers the customers table on
# Base.metadata before any test fixture call_create_all (mirrors the
# pattern in test_intake.py).
from app import models as _models  # noqa: F401
from app.main import app

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
