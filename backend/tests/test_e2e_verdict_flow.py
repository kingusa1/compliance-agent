"""End-to-end smoke for the AI/HUMAN verdict gate.

Walks one rejection through the full lifecycle in the unit-test harness:
  1. Factory creates a Rejection in AI_PENDING with category, fix_required,
     fix_narrative populated and 8 agent_traces rows recorded.
  2. POST /api/rejections/{id}/confirm flips it to HUMAN_CONFIRMED.
  3. (separate row) POST /api/rejections/{id}/override edits a field
     and flips that row to HUMAN_OVERRIDDEN, keeping the new value.

Skips actual LLM calls — patches the 4 helpers so the test runs offline
and deterministic. Real API integration is covered by the manual smoke
checklist in the PR description.
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import AgentTrace, Call, Rejection
from app.rejection_factory import build_rejection_for_call


_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSession = sessionmaker(bind=_engine, autoflush=False, autocommit=False)


@pytest.fixture(autouse=True)
def _bootstrap_schema():
    Base.metadata.create_all(_engine)
    yield
    Base.metadata.drop_all(_engine)


@pytest.fixture
def client():
    def _override():
        s = TestSession()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override

    from app.auth import current_user
    app.dependency_overrides[current_user] = lambda: {
        "id": "e2e-reviewer",
        "email": "rev@example.com",
        "role": "admin",
    }
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_full_lifecycle_factory_through_confirm_and_override(client):
    """Walk one rejection AI_PENDING → HUMAN_CONFIRMED.
    Walk a second one AI_PENDING → HUMAN_OVERRIDDEN with a category swap.
    """
    db = TestSession()
    call_a = Call(id="e2e-call-a", filename="a.mp3", file_path="/tmp/a.mp3")
    call_b = Call(id="e2e-call-b", filename="b.mp3", file_path="/tmp/b.mp3")
    db.add_all([call_a, call_b])
    db.commit()

    failing = [{"name": "DPA confirmation read", "status": "fail", "evidence": "", "notes": ""}]

    async def _llm_stub(prompt: str, timeout: float = 15.0) -> str:
        if "category" in prompt.lower():
            return "PROCESS_FAILURE"
        if "rejection reason" in prompt.lower():
            return "Agent skipped DPA at start of call."
        if "remediation action" in prompt.lower():
            return "AMENDMENT_CALL"
        if "corrective action narrative" in prompt.lower():
            return "Call back customer to amend the missing DPA statement."
        return ""

    # ── Build rejection A via factory (with agent_traces persistence). ──
    with patch("app.rejection_factory._call_llm", side_effect=_llm_stub):
        payload_a = await build_rejection_for_call(
            call_id="e2e-call-a",
            customer_slug=None,
            supplier=None,
            sales_agent=None,
            failing_checkpoints=failing,
            db=db,
        )

    # Factory verifies all 4 LLM-derived fields shipped.
    assert payload_a["category"] == "PROCESS_FAILURE"
    assert payload_a["fix_required"] == "AMENDMENT_CALL"
    assert payload_a["fix_narrative"].startswith("Call back customer")
    assert payload_a["verdict_state"] == "AI_PENDING"

    # 8 agent_traces rows: 4 LLM calls × (user prompt + assistant response).
    traces = db.query(AgentTrace).filter(AgentTrace.call_id == "e2e-call-a").all()
    assert len(traces) == 8

    # Persist as a Rejection row so the API endpoint can confirm it.
    rej_a = Rejection(id=uuid.uuid4(), **payload_a)
    db.add(rej_a)
    db.commit()
    rej_a_id = rej_a.id

    # Build rejection B too.
    with patch("app.rejection_factory._call_llm", side_effect=_llm_stub):
        payload_b = await build_rejection_for_call(
            call_id="e2e-call-b",
            customer_slug=None,
            supplier=None,
            sales_agent=None,
            failing_checkpoints=failing,
            db=db,
        )
    rej_b = Rejection(id=uuid.uuid4(), **payload_b)
    db.add(rej_b)
    db.commit()
    rej_b_id = rej_b.id
    db.close()

    # ── Reviewer A: confirm without changes → HUMAN_CONFIRMED. ─────────
    resp = client.post(f"/api/rejections/{rej_a_id}/confirm")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["verdict_state"] == "HUMAN_CONFIRMED"
    assert body["confirmed_by"] == "e2e-reviewer"
    # Category/fix/narrative all unchanged from AI verdict.
    assert body["category"] == "PROCESS_FAILURE"
    assert body["fix_required"] == "AMENDMENT_CALL"

    # ── Reviewer B: override category + narrative → HUMAN_OVERRIDDEN. ──
    resp = client.post(
        f"/api/rejections/{rej_b_id}/override",
        json={
            "category": "COMPLIANCE_ERROR",
            "fix_narrative": "amendment + confirmation call · resend docusign",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["verdict_state"] == "HUMAN_OVERRIDDEN"
    assert body["category"] == "COMPLIANCE_ERROR"  # human swap stuck
    assert "amendment" in body["fix_narrative"]
    assert body["confirmed_by"] == "e2e-reviewer"
