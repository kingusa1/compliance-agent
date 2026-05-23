"""Tests for GET /api/admin/realtime-status.

The endpoint reports four diagnostic fields:
  - alembic_head
  - publication_tables (Postgres-only)
  - rls_enabled_tables (Postgres-only)
  - policy_count (Postgres-only)
  - composite_indexes (2026-05-24 addition — verifies the
    2026_05_23_q_perf_idx migration's three composite indexes are
    actually present on the live ``calls`` table)

Most of those catalog queries don't exist on SQLite, so under the
in-memory test engine they take the graceful ``except`` path and
report ``error: ...`` instead of raising. The asserts here are
shape-level: every field must be present in the JSON regardless of
which engine ran it, so monitoring tools can rely on the contract.

Auth: only ``lead`` and ``admin`` may call. Reviewer must 403.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Profile


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
    """Same reasoning as test_rejections.py: keep the Profile.role gates
    honest so the reviewer 403 test actually exercises the gate."""
    monkeypatch.setattr("app.config.settings.dev_all_admin", False)
    yield


@pytest.fixture
def seed_profiles_local():
    db = TestSessionLocal()
    try:
        db.add_all([
            Profile(id="sarah", email="sarah@test.local", name="Sarah",
                    role="reviewer", active=True),
            Profile(id="omar", email="omar@test.local", name="Omar",
                    role="lead", active=True),
            Profile(id="zoe", email="zoe@test.local", name="Zoe",
                    role="admin", active=True),
        ])
        db.commit()
    finally:
        db.close()


def test_realtime_status_requires_lead_or_admin(
    mock_jwks, seed_profiles_local, auth
):
    """Reviewer is explicitly blocked: lead+admin only."""
    r = client.get("/api/admin/realtime-status", headers=auth("sarah"))
    assert r.status_code == 403


def test_realtime_status_returns_composite_indexes_field(
    mock_jwks, seed_profiles_local, auth
):
    """Carry-over verification: every response must surface a
    ``composite_indexes`` block with the three expected index names so
    the resume-handover step ("confirm indexes applied on Railway") is
    a single curl.

    On SQLite the pg_indexes query falls through to the error branch —
    that's the contract: structure stays consistent, error is captured.
    """
    r = client.get("/api/admin/realtime-status", headers=auth("zoe"))
    assert r.status_code == 200, r.text
    body = r.json()

    assert "composite_indexes" in body
    block = body["composite_indexes"]
    assert block["expected"] == [
        "ix_calls_queue_lookup",
        "ix_calls_deal_created_at",
        "ix_calls_completed_with_transcript",
    ]
    # SQLite has no pg_indexes catalog → error is reported, not raised.
    # Either it succeeded (Postgres-backed test run) or it's an error
    # string — both are valid response shapes for monitoring.
    assert "present" in block or "error" in block


def test_realtime_status_includes_alembic_head_field(
    mock_jwks, seed_profiles_local, auth
):
    """Field always present even when the alembic_version table is
    missing on SQLite — sanity check that the older diagnostics didn't
    regress when the composite_indexes block was added."""
    r = client.get("/api/admin/realtime-status", headers=auth("omar"))
    assert r.status_code == 200
    body = r.json()
    assert "alembic_head" in body
    assert "publication_tables" in body
    assert "rls_enabled_tables" in body
    assert "policy_count" in body
