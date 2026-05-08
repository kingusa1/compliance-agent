"""Phase J Task 32 — prompt versioning on every verdict.

Covers:
  * version_for_supplier() is deterministic (hash is stable across calls).
  * Unknown suppliers fall back to "_default" — sharing the generic-playbook
    hash.
  * Editing a prompt constant actually changes the hash (caught via the
    _reset_version_cache helper so the memoization doesn't mask the mutation).
  * Reviewer overrides inherit prompt_version from the prior AI row, which is
    how ops queries "override rate for prompt v X" stay well-defined.

Setup mirrors test_verdict.py: dedicated in-memory SQLite + StaticPool,
`get_db` overridden in an autouse fixture so run order doesn't matter.
"""
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import prompts
from app.database import Base, get_db
from app.main import app
from app.models import Call, Profile, VerdictHistory
from app.prompts import _reset_version_cache, version_for_supplier


# ─── In-memory DB + overrides (mirrors test_verdict.py) ─────────────────────

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
    # Drop any cached hash from prior tests so prompt mutations re-hash.
    _reset_version_cache()
    yield
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture(autouse=True)
def _disable_dev_all_admin(monkeypatch):
    """Wave 4 DEV_ALL_ADMIN flag rewrites every user's role to 'admin'.
    Reviewer-override flow asserts actor_type == 'reviewer' on the
    VerdictHistory row, so we need stored Profile.role to win."""
    monkeypatch.setattr("app.config.settings.dev_all_admin", False)
    yield


@pytest.fixture
def seed_profiles_local():
    db = TestSessionLocal()
    try:
        db.add_all([
            Profile(id="sarah", email="sarah@test.local", name="Sarah Ali",
                    role="reviewer", active=True),
            Profile(id="omar", email="omar@test.local", name="Omar Hassan",
                    role="lead", active=True),
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
    db = TestSessionLocal()
    try:
        db.add(Call(
            id="c1",
            filename="x.mp3",
            file_path="c1/x.mp3",
            transcript="full transcript body ...",
            detected_supplier="E.ON Next",
            checkpoint_results=json.dumps([_SEED_CP]),
        ))
        db.commit()
    finally:
        db.close()


# ─── Pure-function tests ────────────────────────────────────────────────────

def test_version_for_known_supplier_is_stable():
    """Same supplier → same 12-char hash across calls."""
    v1 = version_for_supplier("E.ON Next")
    v2 = version_for_supplier("E.ON Next")
    assert v1 == v2
    assert len(v1) == 12
    # sha256 hex is lowercase [0-9a-f]
    assert all(c in "0123456789abcdef" for c in v1)


def test_version_for_unknown_supplier_uses_default():
    """Unknown / missing suppliers all collapse to the "_default" bucket — the
    generic-playbook + generic-prompt hash."""
    v_none = version_for_supplier(None)
    v_unknown = version_for_supplier("Totally Made Up Supplier Ltd")
    v_empty = version_for_supplier("")
    assert v_none == v_unknown == v_empty
    # And distinct from a known supplier (E.ON Next has its own playbook).
    assert v_none != version_for_supplier("E.ON Next")


def test_hash_changes_when_prompt_constant_changes(monkeypatch):
    """Editing a supplier's prompt constant must produce a different hash —
    the whole point of this feature. Without _reset_version_cache the
    memoization would hide the mutation and lull us into a false positive."""
    baseline = version_for_supplier("E.ON Next")
    _reset_version_cache()
    monkeypatch.setattr(
        prompts,
        "EON_NEXT_MANDATORY",
        prompts.EON_NEXT_MANDATORY + "\n\n# SENTINEL EDIT — v2",
    )
    # Rebuild the routing dict so the mutation is visible via SUPPLIER_PROMPTS.
    mutated = dict(prompts.SUPPLIER_PROMPTS["E.ON Next"])
    mutated["mandatory"] = prompts.EON_NEXT_MANDATORY
    monkeypatch.setitem(prompts.SUPPLIER_PROMPTS, "E.ON Next", mutated)
    after = version_for_supplier("E.ON Next")
    assert after != baseline


# ─── Insert-site tests ──────────────────────────────────────────────────────

def _make_jwt_headers(user: str, auth):
    """Shorthand so the body of each test stays readable."""
    return auth(user)


def test_verdict_insert_stamps_prompt_version(
    mock_jwks, seed_profiles_local, seed_call, auth
):
    """POST /verdict writes a reviewer row + bootstraps an AI row — both
    must carry the supplier's current prompt_version."""
    expected = version_for_supplier("E.ON Next")
    with patch("app.hitl_routes.abstract_and_store_review", new_callable=AsyncMock):
        r = client.post(
            "/api/calls/c1/verdict",
            headers=auth("sarah"),
            json={
                "checkpoint_id": "cp_1",
                "verdict": "fail",
                "reasoning": "missing consent",
            },
        )
    assert r.status_code == 200, r.text

    db = TestSessionLocal()
    try:
        rows = (
            db.query(VerdictHistory)
            .filter_by(call_id="c1", checkpoint_id="cp_1")
            .order_by(VerdictHistory.created_at)
            .all()
        )
        assert len(rows) == 2, "expected bootstrap AI + reviewer rows"
        for row in rows:
            assert row.prompt_version == expected, (
                f"row {row.actor_type} has {row.prompt_version!r}, "
                f"expected {expected!r}"
            )
    finally:
        db.close()


def test_reviewer_override_carries_forward_prompt_version(
    mock_jwks, seed_profiles_local, seed_call, auth
):
    """Reviewer overrides should inherit prompt_version from the prior AI row —
    so "override rate for version X" queries count the reviewer's disagreement
    against the AI's prompt, not the reviewer's own session."""
    db = TestSessionLocal()
    try:
        call = db.query(Call).filter_by(id="c1").one()
        # Pre-seed an AI row with a sentinel version string. This simulates a
        # prior run whose prompt has since changed — we want the reviewer's
        # override to stick with the ORIGINAL version, not the new one.
        db.add(VerdictHistory(
            id="pre-existing-ai-row",
            call_id=call.id,
            checkpoint_id="cp_1",
            actor_type="ai",
            actor_id="agent",
            verdict="pass",
            reasoning="original analysis",
            confidence=0.9,
            evidence_text="excerpt",
            prompt_version="deadbeef1234",  # sentinel — clearly not a real hash
            is_current=True,
        ))
        db.commit()
    finally:
        db.close()

    with patch("app.hitl_routes.abstract_and_store_review", new_callable=AsyncMock):
        r = client.post(
            "/api/calls/c1/verdict",
            headers=auth("sarah"),
            json={
                "checkpoint_id": "cp_1",
                "verdict": "fail",
                "reasoning": "override",
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
        assert rev_row.prompt_version == "deadbeef1234", (
            "reviewer override should inherit the sentinel version from the "
            "prior AI row"
        )
    finally:
        db.close()
