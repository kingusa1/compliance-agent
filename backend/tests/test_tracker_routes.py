"""GET /api/tracker/rows returns the same rows as build_tracker_rows but
serialised as JSON; ?tab= drives the filter; auth required.

Setup mirrors test_rejections.py: dedicated in-memory SQLite + StaticPool,
autouse clean_db fixture overrides ``get_db``. Auth tests authenticate via
the shared ES256 keypair from conftest's ``mock_jwks`` + ``auth`` fixtures.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, UTC

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Call, Customer, CustomerDeal, Profile, Rejection


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
        db.add(Profile(
            id="sarah", email="sarah@test.local", name="Sarah Ali",
            role="reviewer", active=True,
        ))
        db.commit()
    finally:
        db.close()


def test_endpoint_requires_auth():
    r = client.get("/api/tracker/rows")
    assert r.status_code in (401, 403)


def test_endpoint_active_tab_returns_rejection(mock_jwks, seed_profiles_local, auth):
    db = TestSessionLocal()
    try:
        cust = Customer(id=uuid.uuid4(), legal_name="Acme Ltd", slug="acme")
        deal = CustomerDeal(
            id=uuid.uuid4(), customer_id=cust.id,
            customer_name="Acme Ltd", supplier="E.ON Next",
            status="closed_lost",
        )
        call = Call(
            id=str(uuid.uuid4()), filename="t.mp3", file_path="/tmp/t.mp3",
            deal_id=deal.id, agent_name="Sammy", status="completed",
        )
        rej = Rejection(
            id=uuid.uuid4(), call_id=call.id,
            category="VERBAL_SALES_ERROR",
            rejection_reason="Missed disclosure",
            fix_required="AMENDMENT_CALL", status="NOT_STARTED",
            rejected_at=datetime.now(UTC),
            deadline=datetime.now(UTC) + timedelta(days=2),
        )
        db.add_all([cust, deal, call, rej])
        db.commit()
        rej_id = str(rej.id)
    finally:
        db.close()

    r = client.get("/api/tracker/rows?tab=active", headers=auth("sarah"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert "rows" in body
    assert body["tab"] == "active"
    assert len(body["rows"]) >= 1
    row = next((x for x in body["rows"] if x["rejection_id"] == rej_id), None)
    assert row is not None
    assert row["category"] == "VERBAL_SALES_ERROR"
    assert row["customer_name"] == "Acme Ltd"
