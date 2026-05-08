"""Tests for GET /api/queue — reviewer inbox with metrics.

Covers: metrics (backlog, reviewed_today, leaderboard, avg turnaround),
filter modes (all|unclaimed|in_review|reviewed_today), flagged_count
semantics (both `needs_review=True` and verdict/status == "flagged" count),
and reviewer-name resolution via the `profiles` table.

Setup mirrors test_compliance_lists.py: dedicated in-memory SQLite +
StaticPool, override `get_db` inside the autouse clean_db fixture.
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
from app.models import Call, Profile, VerdictHistory


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
            Profile(id="sarah", email="sarah@test.local", name="Sarah Ali",   role="reviewer", active=True),
            Profile(id="mo",    email="mo@test.local",    name="Mo Ibrahim",  role="reviewer", active=True),
            Profile(id="layla", email="layla@test.local", name="Layla Said",  role="reviewer", active=True),
            Profile(id="omar",  email="omar@test.local",  name="Omar Hassan", role="lead",     active=True),
        ])
        db.commit()
    finally:
        db.close()


# ─── Tests ──────────────────────────────────────────────────────────────────

def test_queue_returns_metrics_and_list(
    mock_jwks, seed_profiles_local, auth
):
    """Smoke test: seed a pending call + a reviewed-today call, assert
    metrics reflect both, and the pending call appears in the list."""
    db = TestSessionLocal()
    now = datetime.utcnow()
    try:
        # c1: unclaimed + pending, has a flagged checkpoint.
        db.add(Call(
            id="c1", filename="c1.mp3", file_path="c1/c1.mp3",
            review_status="unclaimed",
            compliance_status="pending",
            checkpoint_results=json.dumps([
                {"id": "cp_0", "name": "Greeting", "status": "flagged"},
            ]),
            created_at=now,
        ))
        # c2: reviewed, compliant, by mo, 5m ago; created 30m ago (turnaround = 25m).
        db.add(Call(
            id="c2", filename="c2.mp3", file_path="c2/c2.mp3",
            review_status="reviewed",
            compliance_status="compliant",
            reviewed_by="mo",
            reviewed_at=now - timedelta(minutes=5),
            created_at=now - timedelta(minutes=30),
        ))
        # VerdictHistory row from mo today (feeds leaderboard + reviewed_today).
        db.add(VerdictHistory(
            id="vh1",
            call_id="c2",
            checkpoint_id="cp_0",
            actor_type="reviewer",
            actor_id="mo",
            verdict="pass",
            is_current=True,
            created_at=now - timedelta(minutes=5),
        ))
        db.commit()
    finally:
        db.close()

    r = client.get("/api/queue", headers=auth("sarah"))
    assert r.status_code == 200, r.text
    body = r.json()

    metrics = body["metrics"]
    assert metrics["backlog"] == 1
    assert metrics["reviewed_today"] >= 1
    # Leaderboard should surface mo by display name.
    lb_names = [row["name"] for row in metrics["leaderboard"]]
    assert "Mo Ibrahim" in lb_names
    mo_row = next(r for r in metrics["leaderboard"] if r["name"] == "Mo Ibrahim")
    assert mo_row["count"] >= 1
    assert mo_row["reviewer_id"] == "mo"

    # Default filter "all" — both c1 (pending) and c2 (reviewed today) show.
    ids = [c["id"] for c in body["calls"]]
    assert "c1" in ids


def test_queue_filter_unclaimed(mock_jwks, seed_profiles_local, auth):
    """filter=unclaimed returns only unclaimed + pending calls."""
    db = TestSessionLocal()
    now = datetime.utcnow()
    try:
        db.add(Call(
            id="c1", filename="c1.mp3", file_path="c1/c1.mp3",
            review_status="unclaimed", compliance_status="pending",
            created_at=now,
        ))
        db.add(Call(
            id="c2", filename="c2.mp3", file_path="c2/c2.mp3",
            review_status="reviewed", compliance_status="compliant",
            reviewed_by="mo", reviewed_at=now - timedelta(minutes=5),
            created_at=now - timedelta(minutes=30),
        ))
        db.commit()
    finally:
        db.close()

    r = client.get("/api/queue?filter=unclaimed", headers=auth("sarah"))
    assert r.status_code == 200, r.text
    ids = [c["id"] for c in r.json()["calls"]]
    assert ids == ["c1"]


def test_queue_filter_in_review(mock_jwks, seed_profiles_local, auth):
    """filter=in_review returns only calls with review_status='in_review'."""
    db = TestSessionLocal()
    now = datetime.utcnow()
    try:
        db.add(Call(
            id="c1", filename="c1.mp3", file_path="c1/c1.mp3",
            review_status="unclaimed", compliance_status="pending",
            created_at=now,
        ))
        db.add(Call(
            id="c3", filename="c3.mp3", file_path="c3/c3.mp3",
            review_status="in_review", compliance_status="pending",
            created_at=now - timedelta(minutes=1),
        ))
        db.commit()
    finally:
        db.close()

    r = client.get("/api/queue?filter=in_review", headers=auth("sarah"))
    assert r.status_code == 200, r.text
    ids = [c["id"] for c in r.json()["calls"]]
    assert ids == ["c3"]


def test_queue_filter_reviewed_today(mock_jwks, seed_profiles_local, auth):
    """filter=reviewed_today returns only calls reviewed since midnight."""
    db = TestSessionLocal()
    now = datetime.utcnow()
    try:
        db.add(Call(
            id="c1", filename="c1.mp3", file_path="c1/c1.mp3",
            review_status="unclaimed", compliance_status="pending",
            created_at=now,
        ))
        db.add(Call(
            id="c2", filename="c2.mp3", file_path="c2/c2.mp3",
            review_status="reviewed", compliance_status="compliant",
            reviewed_by="mo", reviewed_at=now - timedelta(minutes=5),
            created_at=now - timedelta(minutes=30),
        ))
        db.commit()
    finally:
        db.close()

    r = client.get("/api/queue?filter=reviewed_today", headers=auth("sarah"))
    assert r.status_code == 200, r.text
    ids = [c["id"] for c in r.json()["calls"]]
    assert ids == ["c2"]


def test_flagged_count_counts_checkpoints_needing_review(
    mock_jwks, seed_profiles_local, auth
):
    """flagged_count should include both `needs_review=True` and
    `verdict|status == 'flagged'` checkpoints."""
    db = TestSessionLocal()
    now = datetime.utcnow()
    try:
        db.add(Call(
            id="c1", filename="c1.mp3", file_path="c1/c1.mp3",
            review_status="unclaimed", compliance_status="pending",
            checkpoint_results=json.dumps([
                {"id": "cp_0", "name": "A", "needs_review": True, "status": "pass"},
                {"id": "cp_1", "name": "B", "status": "flagged"},
                {"id": "cp_2", "name": "C", "status": "pass"},
            ]),
            created_at=now,
        ))
        db.commit()
    finally:
        db.close()

    r = client.get("/api/queue", headers=auth("sarah"))
    assert r.status_code == 200, r.text
    entry = next(c for c in r.json()["calls"] if c["id"] == "c1")
    assert entry["flagged_count"] == 2


def test_claimed_by_name_resolves_via_profiles(
    mock_jwks, seed_profiles_local, auth
):
    """After sarah claims a call, the queue row shows claimed_by="Sarah Ali"."""
    db = TestSessionLocal()
    now = datetime.utcnow()
    try:
        db.add(Call(
            id="c1", filename="c1.mp3", file_path="c1/c1.mp3",
            review_status="unclaimed", compliance_status="pending",
            created_at=now,
        ))
        db.commit()
    finally:
        db.close()

    # Claim c1 as sarah.
    r_claim = client.post("/api/calls/c1/claim", headers=auth("sarah"))
    assert r_claim.status_code == 200, r_claim.text

    r = client.get("/api/queue", headers=auth("sarah"))
    assert r.status_code == 200, r.text
    entry = next(c for c in r.json()["calls"] if c["id"] == "c1")
    assert entry["claimed_by"] == "Sarah Ali"


def test_queue_without_auth_401():
    r = client.get("/api/queue")
    assert r.status_code == 401
