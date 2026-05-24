"""Tests for POST /api/agent/chat — L10 chat-UI revival.

Covers:
  • SSE token events stream from `agent_chat.run_chat`.
  • SSE citation events emit when a tool returns search results.
  • The 10-iteration cap surfaces an `end` event with finish_reason==max_iterations.
  • call_id in body propagates into the system prompt (so the model is
    nudged to scope query_call / find_similar_failures to that id).

`agent_chat.run_chat` is patched at the symbol used by the route module so we
don't need OpenRouter or pgvector. Tests parse the SSE response body line-by-
line — same wire format as the real client.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent_chat_routes import agent_chat_router
from app.database import Base, get_db
from app.main import app


# ─── Test DB plumbing (mirrors test_agent_trace.py) ─────────────────────────
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


# Mount the chat router on the shared app instance for these tests. Lane E
# mounts it in main.py for prod; we don't depend on that landing first.
# Idempotent: include_router on the same prefix is safe (FastAPI just appends
# routes; duplicates are resolved by the first match).
if not any(getattr(r, "path", "") == "/api/agent/chat" for r in app.routes):
    app.include_router(agent_chat_router)


client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_db():
    app.dependency_overrides[get_db] = _override_get_db
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)

    # 2026-05-24 wiring audit C4 added Depends(current_reviewer) to
    # POST /api/agent/chat. Stub it so the test client authenticates as
    # admin (these tests assert success paths). Pop in teardown so the
    # override doesn't leak into later files.
    from app.auth import current_user, require_lead
    from app.reviewers import current_reviewer

    _stub_admin = {
        "id": "test-admin",
        "email": "test-admin@compliance-agent.local",
        "name": "Test Admin",
        "role": "admin",
    }
    app.dependency_overrides[current_user] = lambda: _stub_admin
    app.dependency_overrides[current_reviewer] = lambda: _stub_admin
    app.dependency_overrides[require_lead] = lambda: _stub_admin
    yield
    app.dependency_overrides.pop(current_user, None)
    app.dependency_overrides.pop(current_reviewer, None)
    app.dependency_overrides.pop(require_lead, None)


# ─── Helpers ────────────────────────────────────────────────────────────────


def _parse_sse(body: str) -> list[tuple[str, dict]]:
    """Parse `event: X\\ndata: {...}\\n\\n` frames into [(event, data), ...]."""
    out: list[tuple[str, dict]] = []
    for frame in body.split("\n\n"):
        evt: str | None = None
        data_str = ""
        for line in frame.splitlines():
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                evt = line[6:].strip()
            elif line.startswith("data:"):
                data_str += line[5:].strip()
        if evt and data_str:
            try:
                out.append((evt, json.loads(data_str)))
            except json.JSONDecodeError:
                pass
    return out


def _make_run_chat(events: list[tuple[str, Any]]):
    """Build a fake `run_chat` async generator that yields the given events."""
    async def fake(messages, db, **kwargs):
        for ev in events:
            yield ev

    return fake


# ─── Tests ──────────────────────────────────────────────────────────────────


def test_chat_route_streams_tokens():
    """SSE response carries token deltas + a final end event."""
    events = [
        ("token", "Hello "),
        ("token", "world."),
        ("done", {"text": "Hello world.", "finish_reason": "stop", "iterations": 1}),
    ]
    with patch("app.agent_chat_routes.agent_chat.run_chat", _make_run_chat(events)):
        r = client.post(
            "/api/agent/chat",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
    assert r.status_code == 200, r.text
    parsed = _parse_sse(r.text)
    types = [t for t, _ in parsed]
    assert "token" in types
    assert types.count("token") == 2
    assert types[-1] == "end"
    deltas = [p["delta"] for t, p in parsed if t == "token"]
    assert deltas == ["Hello ", "world."]
    end_payload = next(p for t, p in parsed if t == "end")
    assert end_payload["finish_reason"] == "stop"
    assert end_payload["iterations"] == 1


def test_chat_route_emits_citations():
    """A tool_call result with `results: [...]` produces citation events."""
    events = [
        ("tool_call", {"name": "search_scripts", "arguments": {"text": "LOA preamble"}}),
        ("token", "Per the script, "),
        ("done", {"text": "Per the script, ", "finish_reason": "stop", "iterations": 2}),
    ]
    fake_dispatch_result = {
        "results": [
            {
                "ref_id": "sc-1",
                "text": "I'm calling on behalf of E.ON Next Energy as a third party",
                "score": 0.91,
                "metadata": {"supplier": "E.ON Next Energy", "checkpoint_idx": 3},
            },
            {
                "ref_id": "sc-2",
                "text": "I'm not the supplier — I'm an independent broker",
                "score": 0.84,
                "metadata": {"supplier": "E.ON Next Energy", "checkpoint_idx": 4},
            },
        ]
    }
    with patch("app.agent_chat_routes.agent_chat.run_chat", _make_run_chat(events)), \
         patch("app.agent.rag_tools.dispatch", return_value=fake_dispatch_result):
        r = client.post(
            "/api/agent/chat",
            json={"messages": [{"role": "user", "content": "show me LOA wording"}]},
        )
    assert r.status_code == 200
    parsed = _parse_sse(r.text)
    types = [t for t, _ in parsed]
    # 1 tool_call + 1 tool_result + 2 citations + 1 token + 1 end
    assert types.count("tool_call") == 1
    assert types.count("tool_result") == 1
    assert types.count("citation") == 2
    cites = [p for t, p in parsed if t == "citation"]
    assert cites[0]["namespace"] == "scripts"  # filled from tool name
    assert cites[0]["ref_id"] == "sc-1"
    assert 0.0 <= cites[0]["score"] <= 1.0
    assert cites[0]["metadata"]["supplier"] == "E.ON Next Energy"
    # tool_result summary mentions count
    tr = next(p for t, p in parsed if t == "tool_result")
    assert "2" in tr["summary"]
    assert tr["ok"] is True


def test_chat_route_iteration_cap_at_10():
    """Route faithfully forwards the run_chat 'done' frame from the 10-iter cap.

    `app.agent.chat.run_chat` enforces MAX_ITERATIONS=10 internally. Here we
    assert the route surfaces the cap event without modification — the front-
    end relies on `finish_reason == 'max_iterations'` to render the warning.
    """
    events = [
        ("done", {"text": "", "finish_reason": "max_iterations", "iterations": 10}),
    ]
    with patch("app.agent_chat_routes.agent_chat.run_chat", _make_run_chat(events)):
        r = client.post(
            "/api/agent/chat",
            json={"messages": [{"role": "user", "content": "loop forever"}]},
        )
    assert r.status_code == 200
    parsed = _parse_sse(r.text)
    end = next(p for t, p in parsed if t == "end")
    assert end["finish_reason"] == "max_iterations"
    assert end["iterations"] == 10


def test_chat_route_call_id_scoping():
    """When call_id is in the body, the system prompt mentions it so the
    model is nudged to scope queries (e.g. query_call) to that id.

    We capture the conversation `run_chat` was called with via a closure.
    """
    captured: dict[str, Any] = {}

    async def capturing_run_chat(messages, db, **kwargs):
        captured["messages"] = list(messages)
        # End immediately — we only care about the prompt.
        yield ("done", {"text": "", "finish_reason": "stop", "iterations": 0})

    with patch("app.agent_chat_routes.agent_chat.run_chat", capturing_run_chat):
        r = client.post(
            "/api/agent/chat",
            json={
                "messages": [{"role": "user", "content": "what about this call?"}],
                "call_id": "abc-123",
            },
        )
    assert r.status_code == 200
    msgs = captured.get("messages") or []
    assert len(msgs) >= 2
    # First message is the auto-injected system prompt.
    assert msgs[0]["role"] == "system"
    assert "abc-123" in msgs[0]["content"]
    # User message preserved verbatim.
    assert msgs[-1]["role"] == "user"
    assert msgs[-1]["content"] == "what about this call?"
