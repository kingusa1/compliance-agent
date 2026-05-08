"""Tests for optimistic locking on Call mutations (Phase J Task 33).

Covers:
  - verdict without If-Match works (backwards compat)
  - verdict with matching If-Match works, revision bumps
  - verdict with mismatched If-Match returns 409
  - revision bumps on claim, verdict, compliance
  - draft does NOT bump revision (autosave would storm 409s)
  - invalid / unparseable If-Match → 400

Setup mirrors test_verdict.py / test_release.py: dedicated in-memory SQLite +
StaticPool, override `get_db` via an autouse fixture so collection order
doesn't matter.
"""
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Call, Profile


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
    """Truncate all tables between tests + re-assert this file's get_db override."""
    app.dependency_overrides[get_db] = _override_get_db
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)
    yield


@pytest.fixture
def seed_profiles_local():
    """Seed 4 profiles; mirrors conftest.seed_profiles but uses this file's engine."""
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


_SEED_CP = {
    "id": "cp_1",
    "name": "Confirm consent",
    "verdict": "pass",
    "confidence": 0.9,
    "reasoning": "heard it",
    "evidence": "excerpt text",
}


@pytest.fixture
def seed_call():
    """Seed one call with a single checkpoint and default revision=1."""
    db = TestSessionLocal()
    try:
        db.add(Call(
            id="c1",
            filename="x.mp3",
            file_path="c1/x.mp3",
            transcript="full transcript body ...",
            detected_supplier="ACME Energy",
            checkpoint_results=json.dumps([_SEED_CP]),
            compliance_status="pending",
            revision=1,
        ))
        db.commit()
    finally:
        db.close()


def _read_revision(call_id: str) -> int:
    db = TestSessionLocal()
    try:
        c = db.query(Call).filter_by(id=call_id).first()
        return c.revision if c else -1
    finally:
        db.close()


# ─── Backwards compat: no If-Match header works ────────────────────────────

def test_verdict_without_if_match_works_backwards_compat(
    mock_jwks, seed_profiles_local, seed_call, auth
):
    """Omitting If-Match is fine — preserves existing clients that don't know
    about revision yet."""
    r = client.post(
        "/api/calls/c1/verdict",
        headers=auth("sarah"),
        json={"checkpoint_id": "cp_1", "verdict": "fail", "reasoning": "test"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["saved"] is True


# ─── If-Match matches → mutation proceeds ──────────────────────────────────

def test_verdict_with_matching_if_match_works(
    mock_jwks, seed_profiles_local, seed_call, auth
):
    """If-Match equal to current revision → mutation succeeds and revision
    bumps by exactly 1."""
    before = _read_revision("c1")
    assert before == 1

    r = client.post(
        "/api/calls/c1/verdict",
        headers={**auth("sarah"), "If-Match": str(before)},
        json={"checkpoint_id": "cp_1", "verdict": "fail", "reasoning": "ok"},
    )
    assert r.status_code == 200, r.text
    assert _read_revision("c1") == before + 1


# ─── If-Match mismatch → 409 ───────────────────────────────────────────────

def test_verdict_with_mismatched_if_match_returns_409(
    mock_jwks, seed_profiles_local, seed_call, auth
):
    """Stale revision → 409 with the current_revision in the detail payload,
    and no state change occurred."""
    # Bump revision once (via claim) so seed is now behind.
    r = client.post("/api/calls/c1/claim", headers=auth("sarah"))
    assert r.status_code == 200, r.text
    current = _read_revision("c1")
    assert current > 1

    r = client.post(
        "/api/calls/c1/verdict",
        headers={**auth("sarah"), "If-Match": "1"},  # stale
        json={"checkpoint_id": "cp_1", "verdict": "fail", "reasoning": "stale"},
    )
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "revision_mismatch"
    assert detail["current_revision"] == current
    assert detail["provided"] == 1

    # Revision unchanged after the rejected mutation.
    assert _read_revision("c1") == current


# ─── Revision bumps on claim ────────────────────────────────────────────────

def test_revision_bumps_on_claim(
    mock_jwks, seed_profiles_local, seed_call, auth
):
    """First-time claim flips review_status → revision should bump."""
    assert _read_revision("c1") == 1
    r = client.post("/api/calls/c1/claim", headers=auth("sarah"))
    assert r.status_code == 200, r.text
    assert _read_revision("c1") == 2


# ─── Revision bumps on verdict ──────────────────────────────────────────────

def test_revision_bumps_on_verdict(
    mock_jwks, seed_profiles_local, seed_call, auth
):
    """A verdict mutates checkpoint_results → revision bumps."""
    before = _read_revision("c1")
    r = client.post(
        "/api/calls/c1/verdict",
        headers=auth("sarah"),
        json={"checkpoint_id": "cp_1", "verdict": "fail"},
    )
    assert r.status_code == 200, r.text
    assert _read_revision("c1") == before + 1


# ─── Revision bumps on compliance ──────────────────────────────────────────

def test_revision_bumps_on_compliance(
    mock_jwks, seed_profiles_local, seed_call, auth
):
    """Final compliance decision mutates compliance_* + review_status → bump."""
    before = _read_revision("c1")
    r = client.post(
        "/api/calls/c1/compliance",
        headers=auth("omar"),  # lead can submit without claim
        json={"status": "compliant", "comment": "looks fine"},
    )
    assert r.status_code == 200, r.text
    assert _read_revision("c1") == before + 1


# ─── Draft does NOT bump revision ──────────────────────────────────────────

def test_draft_does_not_bump_revision(
    mock_jwks, seed_profiles_local, seed_call, auth
):
    """Draft autosave runs every 10s; bumping would storm 409s on concurrent
    clients. Revision MUST stay flat."""
    before = _read_revision("c1")
    r = client.post(
        "/api/calls/c1/draft",
        headers=auth("sarah"),
        json={"checkpoints": [{"id": "cp_1", "reviewer_verdict": "pass"}],
              "comment": "working on it"},
    )
    assert r.status_code == 200, r.text
    assert _read_revision("c1") == before  # unchanged


# ─── Invalid If-Match → 400 ────────────────────────────────────────────────

def test_invalid_if_match_returns_400(
    mock_jwks, seed_profiles_local, seed_call, auth
):
    """Non-integer If-Match → 400 so a bug in the caller is loud, not a
    silent pass."""
    r = client.post(
        "/api/calls/c1/verdict",
        headers={**auth("sarah"), "If-Match": "abc"},
        json={"checkpoint_id": "cp_1", "verdict": "fail"},
    )
    assert r.status_code == 400, r.text
    assert "Invalid If-Match" in r.json()["detail"]
