"""Tests for POST /api/calls/{id}/claim.

Covers: fresh claim, idempotent re-claim by same reviewer, collision with a
different reviewer (409), expired-lock takeover, unknown call (404), and the
missing-auth case (401). All tests use the shared JWT helpers from conftest.

Setup mirrors the pattern in test_routes.py / test_script_versioning.py:
we build an in-memory SQLite engine, override `get_db`, and seed rows
directly through the test SessionLocal so the FastAPI app and the test
body see the same data.
"""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Call, ClaimLock, Profile, ReviewSession


# Dedicated in-memory SQLite — StaticPool + check_same_thread=False so every
# connection in the pool shares one DB instance (FastAPI uses a new session
# per request, tests read/write from their own session; without StaticPool
# each would see a different :memory: DB).
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
    """Truncate all tables between tests + re-assert this file's get_db override.

    Re-assignment matters: test_release.py also sets `app.dependency_overrides[get_db]`
    at import-time pointing at its own in-memory engine. Whichever file was
    imported last wins, which makes cross-file runs order-dependent. Setting
    the override inside an autouse fixture makes every test in this file
    deterministic regardless of collection order.
    """
    app.dependency_overrides[get_db] = _override_get_db
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)
    yield


@pytest.fixture
def seed_profiles_local():
    """Seed 4 profiles into the test SQLite. Mirrors conftest.seed_profiles
    but writes through our test engine (since each test file uses its own).

    Keep in sync with seed_profiles in backend/tests/conftest.py.
    This fixture exists because test_claim.py spins up its own in-memory
    SQLite engine, so the conftest version (which writes to the test_db
    fixture's session) doesn't apply.
    """
    db = TestSessionLocal()
    try:
        db.add_all([
            Profile(id="sarah", email="sarah@test.local", name="Sarah Ali",   role="reviewer", active=True),
            Profile(id="mo",    email="mo@test.local",    name="Mo Ibrahim",  role="reviewer", active=True),
            Profile(id="layla", email="layla@test.local", name="Layla Said",  role="reviewer", active=True),
            Profile(id="omar",  email="omar@test.local",  name="Omar Hassan", role="lead",     active=True),
        ])
        db.commit()
    finally:
        db.close()


@pytest.fixture
def seed_call():
    """Seed a single call row for the claim endpoint to operate on."""
    db = TestSessionLocal()
    try:
        db.add(Call(id="c1", filename="x.mp3", file_path="c1/x.mp3", transcript="..."))
        db.commit()
    finally:
        db.close()


def test_claim_creates_session_and_lock(mock_jwks, seed_profiles_local, seed_call, auth):
    r = client.post("/api/calls/c1/claim", headers=auth("sarah"))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["review_session_id"]
    assert data["call_id"] == "c1"

    db = TestSessionLocal()
    try:
        assert db.query(ClaimLock).filter_by(call_id="c1").count() == 1
        active = db.query(ReviewSession).filter_by(call_id="c1", is_active=True).one()
        assert active.reviewer_id == "sarah"
        call = db.query(Call).filter_by(id="c1").one()
        assert call.review_status == "in_review"
    finally:
        db.close()


def test_second_claim_by_different_reviewer_returns_409(
    mock_jwks, seed_profiles_local, seed_call, auth
):
    r1 = client.post("/api/calls/c1/claim", headers=auth("sarah"))
    assert r1.status_code == 200
    r2 = client.post("/api/calls/c1/claim", headers=auth("mo"))
    assert r2.status_code == 409
    # Detail should mention the current holder's display name from Profile.
    assert "sarah" in r2.json()["detail"].lower()


def test_same_reviewer_reclaim_is_idempotent(
    mock_jwks, seed_profiles_local, seed_call, auth
):
    r1 = client.post("/api/calls/c1/claim", headers=auth("sarah"))
    r2 = client.post("/api/calls/c1/claim", headers=auth("sarah"))
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["review_session_id"] == r2.json()["review_session_id"]


def test_expired_lock_released_and_reclaimable(
    mock_jwks, seed_profiles_local, seed_call, auth
):
    r1 = client.post("/api/calls/c1/claim", headers=auth("sarah"))
    assert r1.status_code == 200

    # Force the lock to expire in the past.
    db = TestSessionLocal()
    try:
        lock = db.query(ClaimLock).filter_by(call_id="c1").one()
        lock.expires_at = datetime.utcnow() - timedelta(minutes=1)
        db.commit()
    finally:
        db.close()

    r2 = client.post("/api/calls/c1/claim", headers=auth("mo"))
    assert r2.status_code == 200, r2.text

    db = TestSessionLocal()
    try:
        active = db.query(ReviewSession).filter_by(call_id="c1", is_active=True).one()
        assert active.reviewer_id == "mo"
        assert db.query(ClaimLock).filter_by(call_id="c1", reviewer_id="mo").count() == 1

        # Sarah's session should have been released with the idle_timeout reason.
        old_session = db.query(ReviewSession).filter_by(
            call_id="c1", reviewer_id="sarah"
        ).one()
        assert old_session.is_active is False
        assert old_session.released_at is not None
        assert old_session.release_reason == "idle_timeout"
    finally:
        db.close()


def test_claim_unknown_call_returns_404(mock_jwks, seed_profiles_local, auth):
    r = client.post("/api/calls/nonexistent/claim", headers=auth("sarah"))
    assert r.status_code == 404


def test_claim_without_auth_returns_401(seed_profiles_local, seed_call):
    r = client.post("/api/calls/c1/claim")
    assert r.status_code == 401
