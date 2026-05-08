"""Tests for POST /api/calls/{id}/verdict.

Covers: reviewer override writes a VerdictHistory row (plus bootstrap AI row)
with is_current flipped, agreement with the AI records a row but DOES NOT fire
learning, call.checkpoint_results is mutated to carry reviewer fields,
verdict-without-claim still saves with review_session_id=None, unknown call →
404, unknown checkpoint → 400, missing auth → 401.

Setup mirrors test_release.py and test_claim.py: dedicated in-memory SQLite +
StaticPool, override `get_db` inside the autouse clean_db fixture so collection
order doesn't matter.
"""
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Call, Profile, ReviewSession, VerdictHistory


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

    Re-assignment matters: test_claim.py and test_release.py also set
    `app.dependency_overrides[get_db]` at import-time, each pointing at its
    own in-memory engine. Whichever file was imported last wins, which makes
    cross-file runs order-dependent. Setting the override inside an autouse
    fixture makes every test in this file deterministic.
    """
    app.dependency_overrides[get_db] = _override_get_db
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)
    yield
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture(autouse=True)
def _disable_dev_all_admin(monkeypatch):
    """Wave 4 added DEV_ALL_ADMIN that rewrites every authenticated user's
    role to 'admin'. These tests assert actor_type == 'reviewer' / 'lead'
    based on stored Profile.role, so disable globally for this file."""
    monkeypatch.setattr("app.config.settings.dev_all_admin", False)
    yield


@pytest.fixture
def seed_profiles_local():
    """Seed 4 profiles into the test SQLite. Mirrors conftest.seed_profiles
    but writes through our test engine (since each test file uses its own).

    Keep in sync with seed_profiles in backend/tests/conftest.py.
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


# A single checkpoint that represents the AI's original verdict; used by most
# tests. Uses the plan's seeded shape (`id` + `verdict` + `confidence`) plus
# an `evidence` field the real pipeline emits — the endpoint reads both.
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
    """Seed a single call with ONE checkpoint (`cp_1`, verdict=pass)."""
    db = TestSessionLocal()
    try:
        db.add(Call(
            id="c1",
            filename="x.mp3",
            file_path="c1/x.mp3",
            transcript="full transcript body ...",
            detected_supplier="ACME Energy",
            checkpoint_results=json.dumps([_SEED_CP]),
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

def test_override_writes_history_and_flips_is_current(
    mock_jwks, seed_profiles_local, seed_call, auth
):
    """Reviewer disagrees with AI → bootstrap AI row (is_current=False) + new
    reviewer row (is_current=True) + abstract_and_store_review called once."""
    _claim("sarah", auth)

    with patch(
        "app.hitl_routes.abstract_and_store_review", new_callable=AsyncMock
    ) as mock_learn:
        r = client.post(
            "/api/calls/c1/verdict",
            headers=auth("sarah"),
            json={
                "checkpoint_id": "cp_1",
                "verdict": "fail",
                "reasoning": "agent skipped cancellation clause",
            },
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["saved"] is True
    assert body["learning_triggered"] is True

    db = TestSessionLocal()
    try:
        rows = (
            db.query(VerdictHistory)
            .filter_by(call_id="c1", checkpoint_id="cp_1")
            .order_by(VerdictHistory.created_at)
            .all()
        )
        # Two rows: the AI bootstrap + the reviewer override.
        assert len(rows) == 2
        ai_row = next(r for r in rows if r.actor_type == "ai")
        rev_row = next(r for r in rows if r.actor_type == "reviewer")
        assert ai_row.is_current is False
        assert ai_row.verdict == "pass"
        assert ai_row.actor_id == "agent"
        assert rev_row.is_current is True
        assert rev_row.verdict == "fail"
        assert rev_row.actor_id == "sarah"
        assert rev_row.reasoning == "agent skipped cancellation clause"
    finally:
        db.close()

    mock_learn.assert_called_once()
    kwargs = mock_learn.call_args.kwargs
    assert kwargs["supplier"] == "ACME Energy"
    assert kwargs["checkpoint_name"] == "Confirm consent"
    assert kwargs["agent_verdict"] == "pass"
    assert kwargs["human_verdict"] == "fail"
    assert kwargs["reviewer_notes"] == "agent skipped cancellation clause"


def test_agree_with_ai_no_learning_triggered(
    mock_jwks, seed_profiles_local, seed_call, auth
):
    """Reviewer confirms AI verdict → row is still written (useful for metrics)
    but no learning extraction fires."""
    _claim("sarah", auth)

    with patch(
        "app.hitl_routes.abstract_and_store_review", new_callable=AsyncMock
    ) as mock_learn:
        r = client.post(
            "/api/calls/c1/verdict",
            headers=auth("sarah"),
            json={
                "checkpoint_id": "cp_1",
                "verdict": "pass",
                "reasoning": "looks correct",
            },
        )
    assert r.status_code == 200, r.text
    assert r.json()["learning_triggered"] is False
    mock_learn.assert_not_called()

    db = TestSessionLocal()
    try:
        # Confirmation is still recorded — we care about "reviewer agreed" metrics.
        rows = (
            db.query(VerdictHistory)
            .filter_by(call_id="c1", checkpoint_id="cp_1")
            .order_by(VerdictHistory.created_at)
            .all()
        )
        assert len(rows) == 2
        assert rows[0].actor_type == "ai"
        assert rows[0].is_current is False
        assert rows[1].actor_type == "reviewer"
        assert rows[1].is_current is True
        assert rows[1].verdict == "pass"
    finally:
        db.close()


def test_verdict_updates_checkpoint_results_current_state(
    mock_jwks, seed_profiles_local, seed_call, auth
):
    """After an override, call.checkpoint_results carries the reviewer fields
    (and the top-level `verdict` is flipped to the reviewer's choice)."""
    _claim("sarah", auth)

    with patch(
        "app.hitl_routes.abstract_and_store_review", new_callable=AsyncMock
    ):
        r = client.post(
            "/api/calls/c1/verdict",
            headers=auth("sarah"),
            json={
                "checkpoint_id": "cp_1",
                "verdict": "fail",
                "reasoning": "not said",
            },
        )
    assert r.status_code == 200, r.text

    db = TestSessionLocal()
    try:
        call = db.query(Call).filter_by(id="c1").one()
        cps = json.loads(call.checkpoint_results)
        assert len(cps) == 1
        cp = cps[0]
        assert cp["verdict"] == "fail"
        assert cp["reviewer_verdict"] == "fail"
        assert cp["reviewer_reasoning"] == "not said"
        assert cp["reviewer_id"] == "sarah"
    finally:
        db.close()


def test_verdict_without_claim_still_saves(
    mock_jwks, seed_profiles_local, seed_call, auth
):
    """No prior /claim → verdict still records (UI enforces claim-before-verdict,
    the API is permissive). review_session_id should be NULL on the new row."""
    # Deliberately skip the /claim step.
    with patch(
        "app.hitl_routes.abstract_and_store_review", new_callable=AsyncMock
    ):
        r = client.post(
            "/api/calls/c1/verdict",
            headers=auth("sarah"),
            json={
                "checkpoint_id": "cp_1",
                "verdict": "fail",
                "reasoning": "no claim",
            },
        )
    assert r.status_code == 200, r.text

    db = TestSessionLocal()
    try:
        rev_row = (
            db.query(VerdictHistory)
            .filter_by(call_id="c1", checkpoint_id="cp_1", actor_type="reviewer")
            .one()
        )
        assert rev_row.review_session_id is None
        assert db.query(ReviewSession).filter_by(call_id="c1").count() == 0
    finally:
        db.close()


def test_verdict_for_unknown_call_returns_404(
    mock_jwks, seed_profiles_local, auth
):
    r = client.post(
        "/api/calls/nonexistent/verdict",
        headers=auth("sarah"),
        json={"checkpoint_id": "cp_1", "verdict": "fail", "reasoning": "x"},
    )
    assert r.status_code == 404


def test_verdict_for_unknown_checkpoint_returns_400(
    mock_jwks, seed_profiles_local, seed_call, auth
):
    r = client.post(
        "/api/calls/c1/verdict",
        headers=auth("sarah"),
        json={"checkpoint_id": "cp_999", "verdict": "fail", "reasoning": "x"},
    )
    assert r.status_code == 400


def test_verdict_without_auth_returns_401(seed_profiles_local, seed_call):
    r = client.post(
        "/api/calls/c1/verdict",
        json={"checkpoint_id": "cp_1", "verdict": "fail", "reasoning": "x"},
    )
    assert r.status_code == 401
