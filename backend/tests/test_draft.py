"""Tests for POST /api/calls/{id}/draft.

Covers: snapshot is persisted verbatim, draft_saved_at is set, review_status
flips "in_review" → "draft" (but NOT from other states like "reviewed"),
unknown call → 404, missing auth → 401, active ReviewSession.last_activity_at
is bumped.

Setup mirrors test_claim.py / test_verdict.py: dedicated in-memory SQLite +
StaticPool, override `get_db` inside the autouse clean_db fixture so
collection order doesn't matter.
"""
import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Call, Profile, ReviewSession


# Dedicated in-memory SQLite — StaticPool + check_same_thread=False so every
# connection in the pool shares one DB instance.
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

    Same rationale as test_claim/test_verdict: other test files set
    app.dependency_overrides[get_db] at import time — doing it inside an
    autouse fixture makes this file deterministic regardless of collection
    order.
    """
    app.dependency_overrides[get_db] = _override_get_db
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)
    yield


@pytest.fixture
def seed_profiles_local():
    """Seed 4 profiles into the test SQLite. Keep in sync with conftest."""
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
    """Seed a single call in the 'in_review' state (as if sarah has claimed it)."""
    db = TestSessionLocal()
    try:
        db.add(Call(
            id="c1",
            filename="x.mp3",
            file_path="c1/x.mp3",
            transcript="...",
            review_status="in_review",
        ))
        db.commit()
    finally:
        db.close()


def _payload() -> dict:
    """A realistic draft payload — the endpoint treats this as an opaque JSON blob."""
    return {
        "checkpoints": [
            {"id": "cp_0", "verdict": "pass", "reasoning": "heard it"},
            {"id": "cp_1", "verdict": "fail", "reasoning": "missed cancellation clause"},
        ],
        "comment": "Still working through the middle section.",
        "notes": {"focus_timestamp": 123.4},
    }


# ─── Tests ──────────────────────────────────────────────────────────────────

def test_draft_saves_snapshot_and_updates_timestamps(
    mock_jwks, seed_profiles_local, seed_call, auth
):
    """Happy path: snapshot JSON persists verbatim, draft_saved_at is set,
    review_status flips to 'draft', response returns saved_at ISO string."""
    before = datetime.utcnow() - timedelta(seconds=1)

    r = client.post("/api/calls/c1/draft", headers=auth("sarah"), json=_payload())
    assert r.status_code == 200, r.text
    body = r.json()
    assert "saved_at" in body
    # ISO string parses back into a datetime.
    saved_at = datetime.fromisoformat(body["saved_at"])
    assert saved_at >= before

    db = TestSessionLocal()
    try:
        call = db.query(Call).filter_by(id="c1").one()
        assert call.draft_snapshot is not None
        snap = json.loads(call.draft_snapshot)
        assert snap["comment"] == "Still working through the middle section."
        assert len(snap["checkpoints"]) == 2
        assert snap["checkpoints"][0]["verdict"] == "pass"
        assert snap["notes"] == {"focus_timestamp": 123.4}
        assert call.draft_saved_at is not None
        assert call.draft_saved_at >= before
        # in_review → draft
        assert call.review_status == "draft"
    finally:
        db.close()


def test_draft_flips_in_review_to_draft_but_not_other_states(
    mock_jwks, seed_profiles_local, auth
):
    """review_status flip is scoped to 'in_review'. A terminal 'reviewed' state
    must NOT be downgraded to 'draft' just because an autosave slips in."""
    db = TestSessionLocal()
    try:
        db.add(Call(
            id="c2",
            filename="y.mp3",
            file_path="c2/y.mp3",
            review_status="reviewed",
        ))
        db.commit()
    finally:
        db.close()

    r = client.post("/api/calls/c2/draft", headers=auth("sarah"), json=_payload())
    assert r.status_code == 200, r.text

    db = TestSessionLocal()
    try:
        call = db.query(Call).filter_by(id="c2").one()
        # Snapshot still written — we don't lose the data even if state is terminal.
        assert call.draft_snapshot is not None
        # …but the terminal state is preserved.
        assert call.review_status == "reviewed"
    finally:
        db.close()


def test_draft_bumps_active_review_session_last_activity(
    mock_jwks, seed_profiles_local, seed_call, auth
):
    """An autosave counts as activity: the reviewer's active ReviewSession
    should have last_activity_at bumped so the idle-timeout sweep doesn't
    reclaim the call out from under them."""
    stale_ts = datetime.utcnow() - timedelta(minutes=20)
    db = TestSessionLocal()
    try:
        db.add(ReviewSession(
            id="rs1",
            call_id="c1",
            reviewer_id="sarah",
            claimed_at=stale_ts,
            last_activity_at=stale_ts,
            is_active=True,
        ))
        db.commit()
    finally:
        db.close()

    r = client.post("/api/calls/c1/draft", headers=auth("sarah"), json=_payload())
    assert r.status_code == 200, r.text

    db = TestSessionLocal()
    try:
        rs = db.query(ReviewSession).filter_by(id="rs1").one()
        assert rs.last_activity_at > stale_ts
    finally:
        db.close()


def test_draft_for_unknown_call_returns_404(
    mock_jwks, seed_profiles_local, auth
):
    r = client.post("/api/calls/nonexistent/draft", headers=auth("sarah"), json=_payload())
    assert r.status_code == 404


def test_draft_without_auth_returns_401(seed_profiles_local, seed_call):
    r = client.post("/api/calls/c1/draft", json=_payload())
    assert r.status_code == 401


def test_draft_overwrites_previous_snapshot(
    mock_jwks, seed_profiles_local, seed_call, auth
):
    """Autosave is idempotent-overwriting: the latest payload replaces whatever
    was there. No history is kept on purpose — VerdictHistory is the audit log;
    the draft column is scratchpad."""
    first = {"checkpoints": [{"id": "cp_0", "verdict": "pass"}], "comment": "v1"}
    second = {"checkpoints": [{"id": "cp_0", "verdict": "fail"}], "comment": "v2"}

    r1 = client.post("/api/calls/c1/draft", headers=auth("sarah"), json=first)
    assert r1.status_code == 200
    r2 = client.post("/api/calls/c1/draft", headers=auth("sarah"), json=second)
    assert r2.status_code == 200

    db = TestSessionLocal()
    try:
        call = db.query(Call).filter_by(id="c1").one()
        snap = json.loads(call.draft_snapshot)
        assert snap["comment"] == "v2"
        assert snap["checkpoints"][0]["verdict"] == "fail"
    finally:
        db.close()
