"""Tests for GET /api/compliant and GET /api/non-compliant list endpoints.

Covers: status filtering (only rows with matching compliance_status come back),
supplier+agent filters as exact matches, pagination via limit/offset, empty
result shape (total=0, calls=[]), and 401 without auth.

Setup mirrors test_verdict.py / test_compliance_override.py: dedicated
in-memory SQLite + StaticPool, override `get_db` inside the autouse clean_db
fixture so collection order doesn't matter.
"""
from datetime import datetime, timedelta

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


@pytest.fixture
def seed_calls():
    """Seed 4 calls spanning every compliance_status we care about:
      c1 compliant  (Tom,  ACME Energy,  duration 42.5, reviewed by sarah)
      c2 compliant  (Mia,  ACME Energy,  duration 30.0, reviewed by sarah)
      c3 non_compliant (Tom, E.ON Next, comment "missed DD", source lead)
      c4 pending    (Tom,  ACME Energy)  ← must never appear in either list.

    created_at is set explicitly so pagination ordering is deterministic
    (DESC by created_at): c1 newest, c2, c3, c4 oldest.
    """
    db = TestSessionLocal()
    now = datetime.utcnow()
    try:
        db.add_all([
            Call(
                id="c1", filename="c1.mp3", file_path="c1/c1.mp3",
                agent_name="Tom", detected_supplier="ACME Energy",
                duration_seconds=42.5,
                compliance_status="compliant",
                compliance_source="reviewer",
                compliance_comment=None,
                reviewed_by="sarah",
                reviewed_at=now - timedelta(minutes=5),
                created_at=now,
                # Reviewer-touched → graduates out of AI_PENDING so it counts
                # toward /api/compliant + /api/non-compliant lists.
                verdict_state="HUMAN_CONFIRMED",
            ),
            Call(
                id="c2", filename="c2.mp3", file_path="c2/c2.mp3",
                agent_name="Mia", detected_supplier="ACME Energy",
                duration_seconds=30.0,
                compliance_status="compliant",
                compliance_source="reviewer",
                compliance_comment=None,
                reviewed_by="sarah",
                reviewed_at=now - timedelta(minutes=10),
                created_at=now - timedelta(minutes=1),
                verdict_state="HUMAN_CONFIRMED",
            ),
            Call(
                id="c3", filename="c3.mp3", file_path="c3/c3.mp3",
                agent_name="Tom", detected_supplier="E.ON Next",
                duration_seconds=55.1,
                compliance_status="non_compliant",
                compliance_source="lead",
                compliance_comment="missed DD",
                reviewed_by="omar",
                reviewed_at=now - timedelta(minutes=15),
                created_at=now - timedelta(minutes=2),
                verdict_state="HUMAN_OVERRIDDEN",
            ),
            Call(
                id="c4", filename="c4.mp3", file_path="c4/c4.mp3",
                agent_name="Tom", detected_supplier="ACME Energy",
                duration_seconds=12.0,
                compliance_status="pending",
                created_at=now - timedelta(minutes=3),
                # Pending = still AI-only; not yet reviewer-touched.
                verdict_state="AI_PENDING",
            ),
        ])
        db.commit()
    finally:
        db.close()


# ─── Tests ──────────────────────────────────────────────────────────────────

def test_compliant_list_returns_only_compliant(
    mock_jwks, seed_profiles_local, seed_calls, auth
):
    r = client.get("/api/compliant", headers=auth("sarah"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2
    ids = [c["id"] for c in body["calls"]]
    assert set(ids) == {"c1", "c2"}
    # Order: DESC by created_at → c1 before c2.
    assert ids == ["c1", "c2"]


def test_non_compliant_returns_comment_and_supplier_filter(
    mock_jwks, seed_profiles_local, seed_calls, auth
):
    r = client.get(
        "/api/non-compliant",
        headers=auth("sarah"),
        params={"supplier": "E.ON Next"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    assert len(body["calls"]) == 1
    entry = body["calls"][0]
    assert entry["id"] == "c3"
    assert entry["comment"] == "missed DD"
    assert entry["supplier"] == "E.ON Next"
    assert entry["agent_name"] == "Tom"
    assert entry["duration"] == 55.1
    assert entry["source"] == "lead"
    assert entry["reviewed_by"] == "omar"
    assert entry["reviewed_at"] is not None
    assert entry["created_at"] is not None


def test_pagination(mock_jwks, seed_profiles_local, seed_calls, auth):
    r1 = client.get(
        "/api/compliant",
        headers=auth("sarah"),
        params={"limit": 1, "offset": 0},
    )
    assert r1.status_code == 200, r1.text
    b1 = r1.json()
    assert b1["total"] == 2
    assert len(b1["calls"]) == 1
    first_id = b1["calls"][0]["id"]

    r2 = client.get(
        "/api/compliant",
        headers=auth("sarah"),
        params={"limit": 1, "offset": 1},
    )
    assert r2.status_code == 200, r2.text
    b2 = r2.json()
    assert b2["total"] == 2
    assert len(b2["calls"]) == 1
    second_id = b2["calls"][0]["id"]

    assert {first_id, second_id} == {"c1", "c2"}
    assert first_id != second_id


def test_agent_filter(mock_jwks, seed_profiles_local, seed_calls, auth):
    r = client.get(
        "/api/compliant",
        headers=auth("sarah"),
        params={"agent": "Tom"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    assert [c["id"] for c in body["calls"]] == ["c1"]


def test_empty_result(mock_jwks, seed_profiles_local, seed_calls, auth):
    r = client.get(
        "/api/non-compliant",
        headers=auth("sarah"),
        params={"supplier": "NoSuchSupplier"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"total": 0, "calls": []}


def test_without_auth_401(seed_profiles_local, seed_calls):
    r_c = client.get("/api/compliant")
    assert r_c.status_code == 401
    r_n = client.get("/api/non-compliant")
    assert r_n.status_code == 401
