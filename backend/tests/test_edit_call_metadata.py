"""PATCH /api/calls/{id}/metadata lets reviewer correct/fill customer
metadata after auto-detect. Updates Call + parent CustomerDeal +
parent Customer rows in one transaction. Tracker rows reflect the
new values on next refresh.

Setup mirrors test_customer_email_on_pass.py — in-memory SQLite +
StaticPool, autouse clean_db fixture overrides ``get_db``. Auth is
exercised via the shared ``mock_jwks`` + ``auth`` fixtures from
conftest.py (ES256-signed test JWT verified against the test public
key).
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Call, Customer, CustomerDeal, Profile


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


def _seed_unknown_call() -> tuple[Customer, CustomerDeal, Call]:
    """Seed an Unknown customer + stub deal + completed call (the auto-
    detect-pending shape that the dialog is meant to fix up)."""
    db = TestSessionLocal()
    try:
        cust = Customer(id=uuid.uuid4(), legal_name="Unknown", slug="unk")
        deal = CustomerDeal(
            id=uuid.uuid4(),
            customer_id=cust.id,
            customer_name="(auto-detect pending abc)",
            supplier="E.ON Next",
            status="in_progress",
        )
        call = Call(
            id=str(uuid.uuid4()),
            filename="t.mp3",
            file_path="/tmp/t.mp3",
            deal_id=deal.id,
            agent_name="Unknown",
            customer_name="Unknown",
            status="completed",
        )
        db.add_all([cust, deal, call])
        db.commit()
        # Detach so callers can use the IDs without a stale session.
        db.refresh(cust)
        db.refresh(deal)
        db.refresh(call)
        return cust, deal, call
    finally:
        db.close()


def test_patch_updates_call_deal_and_customer(mock_jwks, seed_profiles_local, auth):
    cust, deal, call = _seed_unknown_call()

    r = client.patch(
        f"/api/calls/{call.id}/metadata",
        json={
            "customer_name": "Acme Industrial Ltd",
            "agent_name": "Sammy R.",
            "mpan_or_mprn": "1234567890",
            "expected_live_date": "2026-04-30",
            "deal_value_gbp": 42000,
            "supplier": "E.ON Next",
        },
        headers=auth("sarah"),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["call"]["customer_name"] == "Acme Industrial Ltd"
    assert body["call"]["agent_name"] == "Sammy R."

    # Re-read directly from the DB to confirm the rows actually moved.
    db = TestSessionLocal()
    try:
        call_db = db.query(Call).filter_by(id=call.id).first()
        deal_db = db.query(CustomerDeal).filter_by(id=deal.id).first()
        cust_db = db.query(Customer).filter_by(id=cust.id).first()
        assert call_db.customer_name == "Acme Industrial Ltd"
        assert call_db.agent_name == "Sammy R."
        assert deal_db.customer_name == "Acme Industrial Ltd"
        assert deal_db.mpan_or_mprn == "1234567890"
        assert float(deal_db.deal_value_gbp) == 42000
        assert cust_db.legal_name == "Acme Industrial Ltd"
    finally:
        db.close()


def test_patch_requires_auth():
    cust, deal, call = _seed_unknown_call()

    r = client.patch(
        f"/api/calls/{call.id}/metadata",
        json={"customer_name": "x"},
    )
    assert r.status_code == 401


def _seed_named_call(canonical: str) -> tuple[Customer, CustomerDeal, Call]:
    """Variant of _seed_unknown_call where the deal already has a
    canonical business name set (the shrink-guard target shape)."""
    db = TestSessionLocal()
    try:
        cust = Customer(id=uuid.uuid4(), legal_name=canonical, slug="canon")
        deal = CustomerDeal(
            id=uuid.uuid4(),
            customer_id=cust.id,
            customer_name=canonical,
            supplier="E.ON Next",
            status="in_progress",
        )
        call = Call(
            id=str(uuid.uuid4()),
            filename="t.mp3",
            file_path="/tmp/t.mp3",
            deal_id=deal.id,
            agent_name="Sammy R.",
            customer_name=canonical.split()[0],
            status="completed",
        )
        db.add_all([cust, deal, call])
        db.commit()
        db.refresh(cust)
        db.refresh(deal)
        db.refresh(call)
        return cust, deal, call
    finally:
        db.close()


def test_patch_rejects_leading_prefix_shrink(mock_jwks, seed_profiles_local, auth):
    """A reviewer who hits Save without editing the customer_name field
    must not silently shrink the deal canonical from
    "Awais Mustafa Ta Charles Palace" → "Awais"."""
    cust, deal, call = _seed_named_call("Awais Mustafa Ta Charles Palace")

    r = client.patch(
        f"/api/calls/{call.id}/metadata",
        json={"customer_name": "Awais"},
        headers=auth("sarah"),
    )
    assert r.status_code == 422, r.text
    assert "leading-prefix" in r.json()["detail"]

    # DB unchanged — canonical preserved across all 3 rows.
    db = TestSessionLocal()
    try:
        deal_db = db.query(CustomerDeal).filter_by(id=deal.id).first()
        cust_db = db.query(Customer).filter_by(id=cust.id).first()
        assert deal_db.customer_name == "Awais Mustafa Ta Charles Palace"
        assert cust_db.legal_name == "Awais Mustafa Ta Charles Palace"
    finally:
        db.close()


def test_patch_allows_full_canonical_overwrite(mock_jwks, seed_profiles_local, auth):
    """Same canonical → same canonical (no change) is allowed; so is a
    meaningfully different name. Only strict leading-prefix shrinks
    trigger the guard."""
    cust, deal, call = _seed_named_call("Awais Mustafa Ta Charles Palace")

    # Same canonical → no shrink, accepted.
    r = client.patch(
        f"/api/calls/{call.id}/metadata",
        json={"customer_name": "Awais Mustafa Ta Charles Palace"},
        headers=auth("sarah"),
    )
    assert r.status_code == 200, r.text

    # Meaningfully different (not a leading-prefix) → accepted.
    r = client.patch(
        f"/api/calls/{call.id}/metadata",
        json={"customer_name": "Charles Palace Ltd"},
        headers=auth("sarah"),
    )
    assert r.status_code == 200, r.text


def test_patch_collapses_whitespace_and_caps_length(mock_jwks, seed_profiles_local, auth):
    """Empty-after-trim writes None; over-cap fails validation."""
    cust, deal, call = _seed_unknown_call()

    # Whitespace-only → collapsed to "" → writes None to the call row.
    r = client.patch(
        f"/api/calls/{call.id}/metadata",
        json={"agent_name": "   \t  "},
        headers=auth("sarah"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["call"]["agent_name"] is None

    # Over 200-char cap → 422.
    r = client.patch(
        f"/api/calls/{call.id}/metadata",
        json={"agent_name": "x" * 201},
        headers=auth("sarah"),
    )
    assert r.status_code == 422, r.text
