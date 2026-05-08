"""Tests for POST /api/review-sessions/{id}/release.

Covers: owner-initiated release (reason=abandoned), lead-initiated release of
another reviewer's session (reason=lead_reopen), 403 when a non-owner/non-lead
tries, 404 on unknown session, idempotent re-release, and 401 without auth.

Setup mirrors test_claim.py: in-memory SQLite engine + dependency_overrides
for get_db + a local seed_profiles fixture (see note there).
"""
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

    Re-assignment matters: test_claim.py also sets `app.dependency_overrides[get_db]`
    at import-time pointing at its own in-memory engine. Whichever file was
    imported last wins, which makes cross-file runs order-dependent. Setting
    the override inside an autouse fixture makes every test in this file
    deterministic regardless of collection order.
    """
    app.dependency_overrides[get_db] = _override_get_db
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)
    yield
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture(autouse=True)
def _disable_dev_all_admin(monkeypatch):
    """Wave 4 DEV_ALL_ADMIN flag rewrites every user's role to 'admin'.
    test_release_by_other_reviewer_returns_403 needs the 'reviewer' role
    on file so the lock-owner check rejects rather than letting an admin
    walk past the ownership gate."""
    monkeypatch.setattr("app.config.settings.dev_all_admin", False)
    yield


@pytest.fixture
def seed_profiles_local():
    """Seed 4 profiles into the test SQLite. Mirrors conftest.seed_profiles
    but writes through our test engine (since each test file uses its own).

    Keep in sync with seed_profiles in backend/tests/conftest.py.
    This fixture exists because test_release.py spins up its own in-memory
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
    """Seed a single call row that tests will claim + release."""
    db = TestSessionLocal()
    try:
        db.add(Call(id="c1", filename="x.mp3", file_path="c1/x.mp3", transcript="..."))
        db.commit()
    finally:
        db.close()


def _claim(user: str, auth) -> str:
    """Helper: POST /claim for the given reviewer, return the review_session_id."""
    r = client.post("/api/calls/c1/claim", headers=auth(user))
    assert r.status_code == 200, r.text
    return r.json()["review_session_id"]


def test_release_by_owner_clears_lock_and_marks_session_abandoned(
    mock_jwks, seed_profiles_local, seed_call, auth
):
    session_id = _claim("sarah", auth)
    r = client.post(f"/api/review-sessions/{session_id}/release", headers=auth("sarah"))
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "released", "reason": "abandoned"}

    db = TestSessionLocal()
    try:
        assert db.query(ClaimLock).filter_by(call_id="c1").count() == 0
        rs = db.query(ReviewSession).filter_by(id=session_id).one()
        assert rs.is_active is False
        assert rs.released_at is not None
        assert rs.release_reason == "abandoned"
        call = db.query(Call).filter_by(id="c1").one()
        assert call.review_status == "unclaimed"
    finally:
        db.close()


def test_release_by_lead_marks_session_lead_reopen(
    mock_jwks, seed_profiles_local, seed_call, auth
):
    session_id = _claim("sarah", auth)
    r = client.post(f"/api/review-sessions/{session_id}/release", headers=auth("omar"))
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "released", "reason": "lead_reopen"}

    db = TestSessionLocal()
    try:
        assert db.query(ClaimLock).filter_by(call_id="c1").count() == 0
        rs = db.query(ReviewSession).filter_by(id=session_id).one()
        assert rs.is_active is False
        assert rs.released_at is not None
        assert rs.release_reason == "lead_reopen"
        call = db.query(Call).filter_by(id="c1").one()
        assert call.review_status == "unclaimed"
    finally:
        db.close()


def test_release_by_other_reviewer_returns_403(
    mock_jwks, seed_profiles_local, seed_call, auth
):
    session_id = _claim("sarah", auth)
    r = client.post(f"/api/review-sessions/{session_id}/release", headers=auth("mo"))
    assert r.status_code == 403

    # Nothing should have changed.
    db = TestSessionLocal()
    try:
        assert db.query(ClaimLock).filter_by(call_id="c1").count() == 1
        rs = db.query(ReviewSession).filter_by(id=session_id).one()
        assert rs.is_active is True
        assert rs.released_at is None
        assert rs.release_reason is None
    finally:
        db.close()


def test_release_unknown_session_returns_404(mock_jwks, seed_profiles_local, auth):
    r = client.post(
        "/api/review-sessions/does-not-exist/release",
        headers=auth("sarah"),
    )
    assert r.status_code == 404


def test_release_already_released_is_idempotent(
    mock_jwks, seed_profiles_local, seed_call, auth
):
    session_id = _claim("sarah", auth)
    r1 = client.post(f"/api/review-sessions/{session_id}/release", headers=auth("sarah"))
    assert r1.status_code == 200
    assert r1.json() == {"status": "released", "reason": "abandoned"}

    r2 = client.post(f"/api/review-sessions/{session_id}/release", headers=auth("sarah"))
    assert r2.status_code == 200
    assert r2.json() == {"status": "already_released"}


def test_release_without_auth_returns_401(
    mock_jwks, seed_profiles_local, seed_call, auth
):
    session_id = _claim("sarah", auth)
    r = client.post(f"/api/review-sessions/{session_id}/release")
    assert r.status_code == 401
