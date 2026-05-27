"""Tests for PUT /api/calls/{id}/checkpoints/bulk-verdict (wave-25).

Charlotte's feedback 2026-05-27 — "we're wondering if there's a way to
quickly pass something without going through each checkpoint individually."

The endpoint:
  - Resolves CP positions by name (preferred) OR int index (back-compat).
  - Applies one verdict (pass / fail / n_a) to every resolved CP.
  - Returns HTTP 207 with per-item ok/failed buckets (Zalando partial-
    success convention from pre-wave research).
  - Fires LEARNING extraction (fire-and-forget) only for CPs where the
    reviewer disagreed with the AI.
  - Emits a single SSE `verdict_batch_changed` frame (one frame for the
    batch, not N frames).
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
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture(autouse=True)
def _disable_dev_all_admin(monkeypatch):
    monkeypatch.setattr("app.config.settings.dev_all_admin", False)
    yield


@pytest.fixture
def seed_profile():
    db = TestSessionLocal()
    try:
        db.add(Profile(
            id="sarah", email="sarah@test.local", name="Sarah",
            role="reviewer", active=True,
        ))
        db.commit()
    finally:
        db.close()


@pytest.fixture
def seed_call_with_cps():
    """Seed a call with 4 checkpoints — 2 fail, 1 pass, 1 partial."""
    cps = [
        {"name": "Greeting", "status": "fail", "evidence": "no hello"},
        {"name": "Identify supplier", "status": "fail", "evidence": "didn't name"},
        {"name": "Confirm consent", "status": "pass", "evidence": "yes"},
        {"name": "Cooling-off", "status": "partial", "evidence": "14 days only"},
    ]
    db = TestSessionLocal()
    try:
        db.add(Call(
            id="c1",
            filename="x.mp3",
            file_path="c1/x.mp3",
            transcript="…",
            detected_supplier="ACME Energy",
            checkpoint_results=json.dumps(cps),
        ))
        db.commit()
    finally:
        db.close()


def test_bulk_pass_by_name_marks_all_resolved(mock_jwks, seed_profile, seed_call_with_cps, auth):
    """All four CPs flipped to reviewer_verdict=pass via name resolution."""
    with patch(
        "app.routes.abstract_and_store_review", new_callable=AsyncMock
    ) as mock_learn:
        r = client.put(
            "/api/calls/c1/checkpoints/bulk-verdict",
            headers=auth("sarah"),
            json={
                "checkpoint_names": [
                    "Greeting",
                    "Identify supplier",
                    "Confirm consent",
                    "Cooling-off",
                ],
                "checkpoint_indices": [],
                "verdict": "pass",
            },
        )
    assert r.status_code == 207, r.text
    body = r.json()
    assert sorted(body["ok"]) == [0, 1, 2, 3]
    assert body["failed"] == []

    db = TestSessionLocal()
    try:
        cps = json.loads(db.query(Call).filter_by(id="c1").one().checkpoint_results)
        for cp in cps:
            assert cp["reviewer_verdict"] == "pass"
            assert cp["needs_review"] is False
    finally:
        db.close()

    # 3 disagreements (2 fail → pass + 1 partial → pass). The pass-pass
    # row should NOT trigger learning.
    assert mock_learn.call_count == 3


def test_bulk_fail_by_index_partial_success(mock_jwks, seed_profile, seed_call_with_cps, auth):
    """Mix valid + out-of-range indices — endpoint returns 207 partial."""
    with patch("app.routes.abstract_and_store_review", new_callable=AsyncMock):
        r = client.put(
            "/api/calls/c1/checkpoints/bulk-verdict",
            headers=auth("sarah"),
            json={
                "checkpoint_indices": [0, 1, 99],  # 99 is out-of-range
                "verdict": "fail",
            },
        )
    assert r.status_code == 207, r.text
    body = r.json()
    assert sorted(body["ok"]) == [0, 1]
    assert len(body["failed"]) == 1
    assert body["failed"][0]["index"] == 99
    assert body["failed"][0]["reason"] == "index_out_of_range"


def test_bulk_invalid_verdict_400(mock_jwks, seed_profile, seed_call_with_cps, auth):
    r = client.put(
        "/api/calls/c1/checkpoints/bulk-verdict",
        headers=auth("sarah"),
        json={"checkpoint_indices": [0], "verdict": "maybe"},
    )
    assert r.status_code == 400
    assert "Invalid verdict" in r.text


def test_bulk_unknown_call_404(mock_jwks, seed_profile, auth):
    r = client.put(
        "/api/calls/nope/checkpoints/bulk-verdict",
        headers=auth("sarah"),
        json={"checkpoint_indices": [0], "verdict": "pass"},
    )
    assert r.status_code == 404


def test_bulk_no_targets_400(mock_jwks, seed_profile, seed_call_with_cps, auth):
    r = client.put(
        "/api/calls/c1/checkpoints/bulk-verdict",
        headers=auth("sarah"),
        json={"checkpoint_indices": [], "checkpoint_names": [], "verdict": "pass"},
    )
    assert r.status_code == 400
    assert "required" in r.text.lower()


def test_bulk_cap_at_200(mock_jwks, seed_profile, seed_call_with_cps, auth):
    r = client.put(
        "/api/calls/c1/checkpoints/bulk-verdict",
        headers=auth("sarah"),
        json={"checkpoint_indices": list(range(201)), "verdict": "pass"},
    )
    assert r.status_code == 400
    assert "200" in r.text


def test_bulk_unauthenticated_401(seed_call_with_cps):
    r = client.put(
        "/api/calls/c1/checkpoints/bulk-verdict",
        json={"checkpoint_indices": [0], "verdict": "pass"},
    )
    assert r.status_code == 401
