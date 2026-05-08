"""Tests for POST /api/calls/{id}/compliance — human override of the
pipeline's auto-decision.

Covers: reviewer override flips status + writes ComplianceDecision row and
releases the claim; lead override works without claiming; agreeing with the
existing verdict without a comment is allowed and still writes a new row;
disagreeing without a comment returns 422; invalid status → 400; unknown call
→ 404; missing auth → 401.

Setup mirrors test_verdict.py / test_history.py: dedicated in-memory SQLite +
StaticPool, override `get_db` inside the autouse clean_db fixture so
collection order doesn't matter.
"""
import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import (
    Call,
    ClaimLock,
    ComplianceDecision,
    Profile,
    ReviewSession,
)


# Dedicated in-memory SQLite — see test_verdict.py for the StaticPool reasoning.
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

    See test_verdict.py's clean_db fixture for the cross-file override rationale.
    """
    app.dependency_overrides[get_db] = _override_get_db
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)
    yield
    # Pop the override so subsequent test files (e.g. test_deals_stub.py)
    # that read from the real SessionLocal don't transparently get routed
    # to this file's in-memory SQLite.
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture(autouse=True)
def _disable_dev_all_admin(monkeypatch):
    """Wave 4 added DEV_ALL_ADMIN that rewrites every authenticated user's
    role to 'admin'. These tests rely on actual stored roles ('reviewer',
    'lead') to assert compliance_source / actor_type, so disable globally."""
    monkeypatch.setattr("app.config.settings.dev_all_admin", False)
    yield


@pytest.fixture
def seed_profiles_local():
    """Seed 4 profiles into the test SQLite. Keep in sync with
    conftest.seed_profiles."""
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


_SEED_CPS = [
    {
        "id": "cp_1",
        "name": "Confirm consent",
        "status": "fail",
        "verdict": "fail",
        "confidence": 0.9,
    },
]


@pytest.fixture
def seed_call():
    """Seed a Call that the pipeline has already marked non_compliant/auto."""
    db = TestSessionLocal()
    try:
        db.add(Call(
            id="c1",
            filename="x.mp3",
            file_path="c1/x.mp3",
            duration_seconds=42.0,
            transcript="full transcript body ...",
            detected_supplier="ACME Energy",
            checkpoint_results=json.dumps(_SEED_CPS),
            compliance_status="non_compliant",
            compliance_source="auto",
        ))
        db.commit()
    finally:
        db.close()


def _claim(user: str, auth) -> str:
    """Helper: POST /claim for the given reviewer, return the review_session_id."""
    r = client.post("/api/calls/c1/claim", headers=auth(user))
    assert r.status_code == 200, r.text
    return r.json()["review_session_id"]


# ─── Tests ──────────────────────────────────────────────────────────────────

def test_reviewer_can_override_with_comment_to_compliant(
    mock_jwks, seed_profiles_local, seed_call, auth
):
    """Sarah claims the call, disagrees with the auto verdict, provides the
    required comment → call flips to compliant/reviewer, decision row is
    written, claim is released with reason=submitted."""
    session_id = _claim("sarah", auth)

    r = client.post(
        "/api/calls/c1/compliance",
        headers=auth("sarah"),
        json={"status": "compliant", "comment": "Customer confirmed at 03:12"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"saved": True, "compliance_status": "compliant"}

    db = TestSessionLocal()
    try:
        call = db.query(Call).filter_by(id="c1").one()
        assert call.compliance_status == "compliant"
        assert call.compliance_source == "reviewer"
        assert call.compliance_comment == "Customer confirmed at 03:12"
        assert call.compliance_decided_by == "sarah"
        assert isinstance(call.compliance_decided_at, datetime)
        assert call.review_status == "reviewed"
        assert call.reviewed_by == "sarah"
        assert isinstance(call.reviewed_at, datetime)

        current_rows = (
            db.query(ComplianceDecision)
            .filter_by(call_id="c1", is_current=True)
            .all()
        )
        assert len(current_rows) == 1
        cd = current_rows[0]
        assert cd.status == "compliant"
        assert cd.actor_type == "reviewer"
        assert cd.actor_id == "sarah"
        assert cd.comment == "Customer confirmed at 03:12"

        assert db.query(ClaimLock).filter_by(call_id="c1").count() == 0
        rs = db.query(ReviewSession).filter_by(id=session_id).one()
        assert rs.is_active is False
        assert rs.release_reason == "submitted"
        assert isinstance(rs.released_at, datetime)
    finally:
        db.close()


def test_lead_can_override(
    mock_jwks, seed_profiles_local, seed_call, auth
):
    """Leads have override power without first claiming — they can jump in
    and resolve a call directly."""
    r = client.post(
        "/api/calls/c1/compliance",
        headers=auth("omar"),
        json={"status": "compliant", "comment": "Lead resolution"},
    )
    assert r.status_code == 200, r.text

    db = TestSessionLocal()
    try:
        cd = (
            db.query(ComplianceDecision)
            .filter_by(call_id="c1", is_current=True)
            .one()
        )
        assert cd.actor_type == "lead"
        assert cd.actor_id == "omar"
        call = db.query(Call).filter_by(id="c1").one()
        assert call.compliance_source == "lead"
        assert call.compliance_decided_by == "omar"
    finally:
        db.close()


def test_agreeing_with_existing_status_no_comment_allowed(
    mock_jwks, seed_profiles_local, seed_call, auth
):
    """Reviewer confirms the auto non_compliant verdict without a comment →
    200, new decision row is inserted, Call.compliance_source flips to
    'reviewer' even though the verdict itself didn't change."""
    r = client.post(
        "/api/calls/c1/compliance",
        headers=auth("sarah"),
        json={"status": "non_compliant"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"saved": True, "compliance_status": "non_compliant"}

    db = TestSessionLocal()
    try:
        call = db.query(Call).filter_by(id="c1").one()
        assert call.compliance_status == "non_compliant"
        assert call.compliance_source == "reviewer"
        assert call.compliance_decided_by == "sarah"

        current_rows = (
            db.query(ComplianceDecision)
            .filter_by(call_id="c1", is_current=True)
            .all()
        )
        assert len(current_rows) == 1
        assert current_rows[0].actor_type == "reviewer"
        assert current_rows[0].actor_id == "sarah"
        assert current_rows[0].comment is None
    finally:
        db.close()


def test_disagreeing_without_comment_returns_422(
    mock_jwks, seed_profiles_local, seed_call, auth
):
    """Reviewer tries to flip non_compliant → compliant without explaining why
    → 422, no decision row inserted, Call stays put."""
    r = client.post(
        "/api/calls/c1/compliance",
        headers=auth("sarah"),
        json={"status": "compliant"},
    )
    assert r.status_code == 422

    db = TestSessionLocal()
    try:
        assert db.query(ComplianceDecision).filter_by(call_id="c1").count() == 0
        call = db.query(Call).filter_by(id="c1").one()
        assert call.compliance_status == "non_compliant"
        assert call.compliance_source == "auto"
    finally:
        db.close()


def test_invalid_status_returns_400(
    mock_jwks, seed_profiles_local, seed_call, auth
):
    r = client.post(
        "/api/calls/c1/compliance",
        headers=auth("sarah"),
        json={"status": "maybe", "comment": "unsure"},
    )
    assert r.status_code == 400


def test_unknown_call_returns_404(
    mock_jwks, seed_profiles_local, auth
):
    r = client.post(
        "/api/calls/nope/compliance",
        headers=auth("sarah"),
        json={"status": "compliant", "comment": "x"},
    )
    assert r.status_code == 404


def test_without_auth_returns_401(seed_profiles_local, seed_call):
    r = client.post(
        "/api/calls/c1/compliance",
        json={"status": "compliant", "comment": "x"},
    )
    assert r.status_code == 401
