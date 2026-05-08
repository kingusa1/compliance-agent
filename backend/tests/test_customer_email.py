"""W3.B (v3-watt-coverage) — customer confirmation email endpoint tests.

Mirrors test_rejections.py setup: dedicated in-memory SQLite + StaticPool
+ autouse clean_db fixture overriding ``get_db``. The shared ES256
keypair from conftest.py signs auth tokens via ``mock_jwks``.

Coverage:
    - 401 without auth
    - 200 with auth, response shape (sent / message_id / preview_html)
    - HTML body contains customer name + cooling-off language
    - HTML body surfaces extracted unit_rate + standing_charge when present
    - HTML body shows ``{{ MISSING: <key> }}`` placeholder when fields absent
    - missing_fields list in response payload mirrors the placeholders
    - 404 when call doesn't exist
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Call, Customer, CustomerDeal, ExtractedEntity, Profile


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


def _seed_full_call(call_id: str = "call-w3b-1") -> str:
    """Seed a Call linked through CustomerDeal → Customer with rates and a
    DocuSign reference so the template renders without MISSING tokens."""
    db = TestSessionLocal()
    try:
        customer = Customer(
            legal_name="Acme Bakeries Ltd",
            slug="acme-bakeries-ltd",
        )
        db.add(customer)
        db.flush()
        deal = CustomerDeal(
            customer_id=customer.id,
            customer_name="Acme Bakeries Ltd",
            supplier="E.ON Next Energy",
            term_months=24,
            docusign_reference="DOCU-2026-00123",
        )
        db.add(deal)
        db.flush()
        call = Call(
            id=call_id,
            filename="acme.mp3",
            file_path="/tmp/acme.mp3",
            agent_name="Sammie",
            customer_name="Acme Bakeries Ltd",
            detected_supplier="E.ON Next Energy",
            call_ref="CA-2026-0042",
            deal_id=deal.id,
        )
        db.add(call)
        db.add_all([
            ExtractedEntity(call_id=call_id, key="unit_rate",       value="28.4", confidence=0.95, source="regex"),
            ExtractedEntity(call_id=call_id, key="standing_charge", value="42.7", confidence=0.95, source="regex"),
        ])
        db.commit()
        return call_id
    finally:
        db.close()


def _seed_bare_call(call_id: str = "call-w3b-2") -> str:
    """Seed a Call with no deal / no extracted rates so we can prove the
    MISSING placeholders show up everywhere they should."""
    db = TestSessionLocal()
    try:
        db.add(Call(
            id=call_id,
            filename="bare.mp3",
            file_path="/tmp/bare.mp3",
        ))
        db.commit()
        return call_id
    finally:
        db.close()


# ─── auth ───────────────────────────────────────────────────────────────


def test_401_without_auth():
    cid = _seed_full_call()
    r = client.post(f"/api/calls/{cid}/customer-email", json={})
    assert r.status_code == 401


def test_404_when_call_missing(mock_jwks, seed_profiles_local, auth):
    r = client.post("/api/calls/does-not-exist/customer-email", json={}, headers=auth("sarah"))
    assert r.status_code == 404


# ─── happy path ─────────────────────────────────────────────────────────


def test_200_renders_full_template(mock_jwks, seed_profiles_local, auth):
    cid = _seed_full_call()
    r = client.post(
        f"/api/calls/{cid}/customer-email",
        json={"to": "billing@acme-bakeries.example"},
        headers=auth("sarah"),
    )
    assert r.status_code == 200, r.text
    body = r.json()

    # response shape
    assert set(body.keys()) >= {"sent", "message_id", "preview_html", "to", "cc", "missing_fields"}
    assert body["sent"] is True  # to was supplied
    assert body["message_id"].startswith("msg_")
    assert body["to"] == "billing@acme-bakeries.example"
    assert body["missing_fields"] == []

    html = body["preview_html"]
    # Customer + supplier + contract length surface from the linked records.
    assert "Acme Bakeries Ltd" in html
    assert "E.ON Next Energy" in html
    assert "24 months" in html
    # Pricing flows from extracted_entities.
    assert "28.4" in html
    assert "p / kWh" in html
    assert "42.7" in html
    assert "p / day" in html
    # DocuSign + call reference both rendered.
    assert "DOCU-2026-00123" in html
    assert "CA-2026-0042" in html
    # Compliance-mandated cooling-off language. Asserted as two adjacent
    # tokens because the source HTML wraps the phrase across lines.
    assert "14-day cooling-off" in html
    assert "Consumer Contracts" in html
    assert "Regulations 2013" in html
    # Sender block uses the auth'd profile's email.
    assert "sarah@test.local" in html


# ─── failure-mode (missing data) ────────────────────────────────────────


def test_missing_fields_render_placeholders(mock_jwks, seed_profiles_local, auth):
    cid = _seed_bare_call()
    r = client.post(f"/api/calls/{cid}/customer-email", json={}, headers=auth("sarah"))
    assert r.status_code == 200, r.text
    body = r.json()

    # Without an override `to`, sent stays False — the customer email
    # column doesn't exist yet (failure-mode plan).
    assert body["sent"] is False
    assert body["to"] is None

    html = body["preview_html"]
    # Each absent value must surface as the visible placeholder so the
    # reviewer can spot what to backfill before clicking send.
    assert "{{ MISSING: customer_name }}" in html
    assert "{{ MISSING: supplier }}" in html
    assert "{{ MISSING: contract_length }}" in html
    assert "{{ MISSING: unit_rate }}" in html
    assert "{{ MISSING: standing_charge }}" in html
    assert "{{ MISSING: docusign_ref }}" in html
    # Cooling-off paragraph still rendered — it's static.
    assert "14-day cooling-off" in html

    expected_missing = {
        "customer_name", "supplier", "contract_length",
        "unit_rate", "standing_charge", "docusign_ref",
    }
    assert expected_missing.issubset(set(body["missing_fields"]))


def test_invalid_to_address_rejected(mock_jwks, seed_profiles_local, auth):
    cid = _seed_full_call()
    r = client.post(
        f"/api/calls/{cid}/customer-email",
        json={"to": "not-an-email"},
        headers=auth("sarah"),
    )
    assert r.status_code == 422
