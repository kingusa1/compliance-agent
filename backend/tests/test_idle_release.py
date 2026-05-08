"""Tests for POST /api/internal/release-idle-claims.

Covers: expired-lock cleanup (marks session idle_timeout + resets call status),
valid locks are preserved, bulk cleanup across multiple calls, auth gate, and
the edge case where a lock is stale but the call is already in a terminal
review state (we don't trample terminal statuses like "reviewed").

Setup mirrors test_release.py: dedicated in-memory SQLite + StaticPool, override
`get_db` inside the autouse clean_db fixture so cross-file test order doesn't
bite us.
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
    """Same 4-profile seed every HITL test file uses. See conftest.seed_profiles."""
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


def _seed_claim(
    call_id: str,
    reviewer_id: str,
    session_id: str,
    *,
    expires_delta: timedelta,
    last_activity_delta: timedelta = timedelta(minutes=45),
    call_review_status: str = "in_review",
) -> None:
    """Helper: insert a Call + ReviewSession + ClaimLock triple.

    `expires_delta` is added to `now` — negative = past (expired), positive
    = future (valid). `last_activity_delta` is subtracted from now (so the
    default 45min means "reviewer went idle 45min ago").
    """
    now = datetime.utcnow()
    db = TestSessionLocal()
    try:
        db.add(Call(
            id=call_id,
            filename=f"{call_id}.mp3",
            file_path=f"{call_id}/{call_id}.mp3",
            review_status=call_review_status,
        ))
        db.add(ReviewSession(
            id=session_id,
            call_id=call_id,
            reviewer_id=reviewer_id,
            claimed_at=now - last_activity_delta,
            last_activity_at=now - last_activity_delta,
            is_active=True,
        ))
        db.add(ClaimLock(
            call_id=call_id,
            reviewer_id=reviewer_id,
            review_session_id=session_id,
            claimed_at=now - last_activity_delta,
            expires_at=now + expires_delta,
        ))
        db.commit()
    finally:
        db.close()


def test_release_idle_claims_clears_expired(mock_jwks, seed_profiles_local, auth):
    """Expired lock → session inactive (reason=idle_timeout), call unclaimed,
    lock row gone. Returns {"released": 1}."""
    _seed_claim("c1", "sarah", "rs1", expires_delta=timedelta(minutes=-15))

    r = client.post(
        "/api/internal/release-idle-claims",
        headers=auth("sarah"),
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"released": 1}

    db = TestSessionLocal()
    try:
        assert db.query(ClaimLock).filter_by(call_id="c1").count() == 0
        rs = db.query(ReviewSession).filter_by(id="rs1").one()
        assert rs.is_active is False
        assert rs.released_at is not None
        assert rs.release_reason == "idle_timeout"
        call = db.query(Call).filter_by(id="c1").one()
        assert call.review_status == "unclaimed"
    finally:
        db.close()


def test_release_idle_claims_ignores_valid_locks(mock_jwks, seed_profiles_local, auth):
    """Lock not yet expired → nothing changes."""
    _seed_claim("c1", "sarah", "rs1", expires_delta=timedelta(minutes=15))

    r = client.post(
        "/api/internal/release-idle-claims",
        headers=auth("sarah"),
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"released": 0}

    db = TestSessionLocal()
    try:
        assert db.query(ClaimLock).filter_by(call_id="c1").count() == 1
        rs = db.query(ReviewSession).filter_by(id="rs1").one()
        assert rs.is_active is True
        assert rs.released_at is None
        assert rs.release_reason is None
        call = db.query(Call).filter_by(id="c1").one()
        assert call.review_status == "in_review"
    finally:
        db.close()


def test_release_idle_claims_handles_multiple(mock_jwks, seed_profiles_local, auth):
    """Three expired + one valid → only the three are swept."""
    _seed_claim("c1", "sarah", "rs1", expires_delta=timedelta(minutes=-20))
    _seed_claim("c2", "mo",    "rs2", expires_delta=timedelta(minutes=-5))
    _seed_claim("c3", "layla", "rs3", expires_delta=timedelta(minutes=-1))
    _seed_claim("c4", "sarah", "rs4", expires_delta=timedelta(minutes=25))

    r = client.post(
        "/api/internal/release-idle-claims",
        headers=auth("omar"),
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"released": 3}

    db = TestSessionLocal()
    try:
        remaining = {cl.call_id for cl in db.query(ClaimLock).all()}
        assert remaining == {"c4"}
        # Expired calls flipped back to unclaimed.
        for cid in ("c1", "c2", "c3"):
            assert db.query(Call).filter_by(id=cid).one().review_status == "unclaimed"
        # Still-valid call untouched.
        assert db.query(Call).filter_by(id="c4").one().review_status == "in_review"
        # Expired sessions marked idle_timeout.
        for sid in ("rs1", "rs2", "rs3"):
            rs = db.query(ReviewSession).filter_by(id=sid).one()
            assert rs.is_active is False
            assert rs.release_reason == "idle_timeout"
        # Valid session still active.
        assert db.query(ReviewSession).filter_by(id="rs4").one().is_active is True
    finally:
        db.close()


def test_release_idle_claims_requires_auth(mock_jwks, seed_profiles_local):
    """No bearer token → 401, no cleanup runs."""
    _seed_claim("c1", "sarah", "rs1", expires_delta=timedelta(minutes=-15))

    r = client.post("/api/internal/release-idle-claims")
    assert r.status_code == 401

    # Stale lock is still there — auth gate prevented the sweep.
    db = TestSessionLocal()
    try:
        assert db.query(ClaimLock).filter_by(call_id="c1").count() == 1
    finally:
        db.close()


def test_release_idle_claims_only_resets_in_review_calls(
    mock_jwks, seed_profiles_local, auth
):
    """Edge case: call is somehow already "reviewed" but a stale lock lingers
    (possible if state got messy). The sweep deletes the lock + marks the
    session idle, but MUST NOT downgrade a terminal "reviewed" status back to
    "unclaimed"."""
    _seed_claim(
        "c1", "sarah", "rs1",
        expires_delta=timedelta(minutes=-15),
        call_review_status="reviewed",
    )

    r = client.post(
        "/api/internal/release-idle-claims",
        headers=auth("sarah"),
    )
    assert r.status_code == 200
    assert r.json() == {"released": 1}

    db = TestSessionLocal()
    try:
        # Lock cleaned up + session marked idle, as expected…
        assert db.query(ClaimLock).filter_by(call_id="c1").count() == 0
        rs = db.query(ReviewSession).filter_by(id="rs1").one()
        assert rs.is_active is False
        assert rs.release_reason == "idle_timeout"
        # …but the terminal review_status is preserved.
        assert db.query(Call).filter_by(id="c1").one().review_status == "reviewed"
    finally:
        db.close()
