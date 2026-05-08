"""Tests for GET /api/calls/{call_id}/agent-trace.

Covers: empty trace returns [], seeded rows come back ordered by
(run_id, turn) asc, checkpoint_id filter narrows to one run, unknown call
→ 404, missing auth → 401.

Setup mirrors test_draft.py / test_verdict.py: dedicated in-memory SQLite +
StaticPool, override `get_db` inside the autouse clean_db fixture so
collection order doesn't matter.

Backend tests do NOT invoke `run_agent_on_batch` — we seed AgentTrace rows
directly via the session. The agent-loop path is covered by its own tests;
this file asserts the read endpoint's shape and ordering only.
"""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import AgentTrace, Call, Profile


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
            Profile(id="omar",  email="omar@test.local",  name="Omar Hassan", role="lead",     active=True),
        ])
        db.commit()
    finally:
        db.close()


@pytest.fixture
def seed_call():
    db = TestSessionLocal()
    try:
        db.add(Call(
            id="c1",
            filename="x.mp3",
            file_path="c1/x.mp3",
            transcript="Agent: hi...",
        ))
        db.commit()
    finally:
        db.close()


def test_get_trace_empty(mock_jwks, seed_profiles_local, seed_call, auth):
    """Call exists, no agent_traces rows → 200 with empty array."""
    r = client.get("/api/calls/c1/agent-trace", headers=auth("sarah"))
    assert r.status_code == 200, r.text
    assert r.json() == {"trace": []}


def test_get_trace_returns_rows(mock_jwks, seed_profiles_local, seed_call, auth):
    """Seed 3 rows across 2 runs; endpoint returns them ordered by (run_id, turn) asc."""
    base = datetime.utcnow()
    db = TestSessionLocal()
    try:
        # Insert in non-sorted order — the endpoint must sort, not the insert order.
        db.add_all([
            AgentTrace(
                id="t2", call_id="c1", run_id="run-B", turn=0,
                role="assistant", content="hmm",
                model="sonnet", created_at=base + timedelta(seconds=2),
            ),
            AgentTrace(
                id="t1", call_id="c1", run_id="run-A", turn=1,
                role="assistant", content="final",
                model="flash", created_at=base + timedelta(seconds=1),
            ),
            AgentTrace(
                id="t0", call_id="c1", run_id="run-A", turn=0,
                role="user", content="prompt",
                model="flash", created_at=base,
            ),
        ])
        db.commit()
    finally:
        db.close()

    r = client.get("/api/calls/c1/agent-trace", headers=auth("sarah"))
    assert r.status_code == 200, r.text
    trace = r.json()["trace"]
    assert len(trace) == 3
    # Ordering: run-A (turn 0), run-A (turn 1), run-B (turn 0).
    assert [row["id"] for row in trace] == ["t0", "t1", "t2"]
    # Shape check on one entry.
    assert trace[0]["run_id"] == "run-A"
    assert trace[0]["turn"] == 0
    assert trace[0]["role"] == "user"
    assert trace[0]["content"] == "prompt"
    assert trace[0]["model"] == "flash"
    assert trace[0]["tool_name"] is None
    assert trace[0]["tool_input"] is None
    assert trace[0]["tool_output"] is None
    assert trace[0]["latency_ms"] is None
    assert trace[0]["created_at"] is not None


def test_get_trace_filters_by_checkpoint(
    mock_jwks, seed_profiles_local, seed_call, auth,
):
    """Seed rows tagged with checkpoint_id cp_1 AND cp_2; filter returns only cp_1."""
    base = datetime.utcnow()
    db = TestSessionLocal()
    try:
        db.add_all([
            AgentTrace(
                id="cp1-turn0", call_id="c1", checkpoint_id="cp_1",
                run_id="run-A", turn=0, role="user", content="analyze cp_1",
                created_at=base,
            ),
            AgentTrace(
                id="cp1-turn1", call_id="c1", checkpoint_id="cp_1",
                run_id="run-A", turn=1, role="assistant", content="done",
                created_at=base + timedelta(seconds=1),
            ),
            AgentTrace(
                id="cp2-turn0", call_id="c1", checkpoint_id="cp_2",
                run_id="run-B", turn=0, role="user", content="analyze cp_2",
                created_at=base + timedelta(seconds=2),
            ),
        ])
        db.commit()
    finally:
        db.close()

    r = client.get(
        "/api/calls/c1/agent-trace?checkpoint_id=cp_1",
        headers=auth("sarah"),
    )
    assert r.status_code == 200, r.text
    trace = r.json()["trace"]
    assert len(trace) == 2
    assert {row["id"] for row in trace} == {"cp1-turn0", "cp1-turn1"}
    assert all(row["run_id"] == "run-A" for row in trace)


def test_get_trace_unknown_call_returns_404(
    mock_jwks, seed_profiles_local, auth,
):
    r = client.get("/api/calls/does-not-exist/agent-trace", headers=auth("sarah"))
    assert r.status_code == 404


def test_get_trace_requires_auth(seed_profiles_local, seed_call):
    r = client.get("/api/calls/c1/agent-trace")
    assert r.status_code == 401
