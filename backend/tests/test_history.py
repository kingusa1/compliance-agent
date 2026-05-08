"""Tests for GET /api/calls/{id}/history.

Covers: fresh call returns empty arrays, reviewer override populates the
`verdicts` array (AI bootstrap + reviewer row), multiple verdicts come back in
ascending created_at order, entry shapes match the spec, unknown call → 404,
missing auth → 401.

Setup mirrors test_verdict.py / test_release.py / test_claim.py: dedicated
in-memory SQLite + StaticPool, override `get_db` inside the autouse clean_db
fixture so collection order doesn't matter.
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
from app.models import Call, Profile


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
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture(autouse=True)
def _disable_dev_all_admin(monkeypatch):
    """Wave 4 DEV_ALL_ADMIN flag rewrites every user's role to 'admin'.
    History tests assert actor_type == 'reviewer' from stored profile role."""
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


# Two checkpoints so we can post verdicts on different checkpoint_ids and
# verify ordering without stomping on is_current logic for the same checkpoint.
_SEED_CPS = [
    {
        "id": "cp_1",
        "name": "Confirm consent",
        "verdict": "pass",
        "confidence": 0.9,
        "reasoning": "heard it",
        "evidence": "excerpt text",
    },
    {
        "id": "cp_2",
        "name": "Disclose cancellation",
        "verdict": "pass",
        "confidence": 0.8,
        "reasoning": "stated clearly",
        "evidence": "cancel clause text",
    },
]


@pytest.fixture
def seed_call():
    """Seed a single call with TWO checkpoints (cp_1, cp_2)."""
    db = TestSessionLocal()
    try:
        db.add(Call(
            id="c1",
            filename="x.mp3",
            file_path="c1/x.mp3",
            transcript="full transcript body ...",
            detected_supplier="ACME Energy",
            checkpoint_results=json.dumps(_SEED_CPS),
        ))
        db.commit()
    finally:
        db.close()


# ─── Tests ──────────────────────────────────────────────────────────────────

def test_history_empty_for_fresh_call(
    mock_jwks, seed_profiles_local, seed_call, auth
):
    """Call exists but nothing has touched it yet → all three arrays empty."""
    r = client.get("/api/calls/c1/history", headers=auth("sarah"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"verdicts": [], "edits": [], "compliance": []}


def test_history_returns_verdict_after_override(
    mock_jwks, seed_profiles_local, seed_call, auth
):
    """Reviewer submits a verdict → history returns AI bootstrap + reviewer rows."""
    with patch(
        "app.hitl_routes.abstract_and_store_review", new_callable=AsyncMock
    ):
        r = client.post(
            "/api/calls/c1/verdict",
            headers=auth("sarah"),
            json={"checkpoint_id": "cp_1", "verdict": "fail", "reasoning": "missed"},
        )
    assert r.status_code == 200, r.text

    r = client.get("/api/calls/c1/history", headers=auth("sarah"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["verdicts"]) == 2
    assert len(body["edits"]) == 0
    assert len(body["compliance"]) == 0

    actor_types = [v["actor_type"] for v in body["verdicts"]]
    assert "ai" in actor_types
    assert "reviewer" in actor_types


def test_history_orders_verdicts_ascending_by_created_at(
    mock_jwks, seed_profiles_local, seed_call, auth
):
    """Two verdicts on different checkpoints → history returns them in
    creation order (oldest first)."""
    with patch(
        "app.hitl_routes.abstract_and_store_review", new_callable=AsyncMock
    ):
        r1 = client.post(
            "/api/calls/c1/verdict",
            headers=auth("sarah"),
            json={"checkpoint_id": "cp_1", "verdict": "fail", "reasoning": "first"},
        )
        assert r1.status_code == 200, r1.text
        r2 = client.post(
            "/api/calls/c1/verdict",
            headers=auth("sarah"),
            json={"checkpoint_id": "cp_2", "verdict": "fail", "reasoning": "second"},
        )
        assert r2.status_code == 200, r2.text

    r = client.get("/api/calls/c1/history", headers=auth("sarah"))
    assert r.status_code == 200, r.text
    verdicts = r.json()["verdicts"]
    # 4 rows total: AI bootstrap for cp_1, reviewer for cp_1, AI bootstrap for
    # cp_2, reviewer for cp_2 — in that insertion order.
    assert len(verdicts) == 4
    timestamps = [v["created_at"] for v in verdicts]
    assert timestamps == sorted(timestamps), (
        f"verdicts not ordered ASC by created_at: {timestamps}"
    )


def test_history_shape_matches_spec(
    mock_jwks, seed_profiles_local, seed_call, auth
):
    """Each verdict entry exposes exactly the fields in the spec."""
    with patch(
        "app.hitl_routes.abstract_and_store_review", new_callable=AsyncMock
    ):
        r = client.post(
            "/api/calls/c1/verdict",
            headers=auth("sarah"),
            json={"checkpoint_id": "cp_1", "verdict": "fail", "reasoning": "shape"},
        )
        assert r.status_code == 200, r.text

    r = client.get("/api/calls/c1/history", headers=auth("sarah"))
    assert r.status_code == 200, r.text
    body = r.json()
    expected_keys = {
        "id",
        "checkpoint_id",
        "actor_type",
        "actor_id",
        "verdict",
        "reasoning",
        "confidence",
        "is_current",
        "created_at",
    }
    for v in body["verdicts"]:
        assert set(v.keys()) == expected_keys, (
            f"verdict entry missing/extra keys: {set(v.keys()) ^ expected_keys}"
        )
        # Sanity: created_at is a string (ISO) not a datetime.
        assert isinstance(v["created_at"], str)


def test_history_unknown_call_returns_404(
    mock_jwks, seed_profiles_local, auth
):
    r = client.get("/api/calls/nope/history", headers=auth("sarah"))
    assert r.status_code == 404


def test_history_without_auth_returns_401(seed_profiles_local, seed_call):
    r = client.get("/api/calls/c1/history")
    assert r.status_code == 401
