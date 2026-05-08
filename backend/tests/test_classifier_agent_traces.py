"""rejection_factory writes agent_traces rows for the 4 classifier LLM calls.

Mirrors checkpoint_analyzer's pattern so the HITL UI's "AI reasoning"
expander can render the category/fix/reason/narrative reasoning chain
alongside each rejection.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import AgentTrace, Call
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
def db_with_call():
    s = TestSession()
    call = Call(id="trace-call-1", filename="x.mp3", file_path="/tmp/x.mp3")
    s.add(call)
    s.commit()
    yield s
    s.close()


@pytest.mark.asyncio
async def test_factory_persists_agent_traces_when_db_provided(db_with_call):
    """4 LLM calls × 2 turns each (user prompt + assistant response) = 8 rows."""
    failing = [{"name": "X", "status": "fail", "evidence": "", "notes": ""}]

    async def _llm_stub(prompt: str, timeout: float = 15.0) -> str:
        # Return different output per prompt so we can match step → row.
        if "category" in prompt.lower():
            return "PROCESS_FAILURE"
        if "rejection reason" in prompt.lower():
            return "Agent skipped DPA at the start of the call."
        if "remediation action" in prompt.lower():
            return "AMENDMENT_CALL"
        if "corrective action narrative" in prompt.lower():
            return "Call back customer to amend the missing DPA statement."
        return ""

    with patch("app.rejection_factory._call_llm", side_effect=_llm_stub):
        out = await build_rejection_for_call(
            call_id="trace-call-1",
            customer_slug=None,
            supplier=None,
            sales_agent=None,
            failing_checkpoints=failing,
            db=db_with_call,
        )

    assert out["category"] == "PROCESS_FAILURE"

    rows = (
        db_with_call.query(AgentTrace)
        .filter(AgentTrace.call_id == "trace-call-1")
        .order_by(AgentTrace.turn)
        .all()
    )
    # 4 LLM calls × (user prompt turn + assistant response turn) = 8 rows.
    assert len(rows) == 8
    # All from same run_id so HITL UI can group them.
    assert len({r.run_id for r in rows}) == 1
    # Each step appears as a (user, assistant) pair.
    steps = {r.tool_name for r in rows}
    assert steps == {"classify_category", "summarise_reason", "propose_fix", "propose_narrative"}
    # Assistant rows have non-null latency_ms and content.
    assistant_rows = [r for r in rows if r.role == "assistant"]
    assert len(assistant_rows) == 4
    assert all(r.latency_ms is not None and r.latency_ms >= 0 for r in assistant_rows)
    assert all(r.content for r in assistant_rows)
    # User rows hold the prompt text.
    user_rows = [r for r in rows if r.role == "user"]
    assert len(user_rows) == 4
    assert all(r.content for r in user_rows)


@pytest.mark.asyncio
async def test_factory_skips_traces_when_db_omitted():
    """Backward compat: callers that don't pass db get the legacy behavior."""
    failing = [{"name": "X", "status": "fail", "evidence": "", "notes": ""}]
    with patch("app.rejection_factory._classify_category", new_callable=AsyncMock) as cls, \
         patch("app.rejection_factory._summarise_reason", new_callable=AsyncMock) as rsn, \
         patch("app.rejection_factory._propose_fix", new_callable=AsyncMock) as fix, \
         patch("app.rejection_factory._propose_narrative", new_callable=AsyncMock) as nar:
        cls.return_value = "ADMIN_ERROR"
        rsn.return_value = "ok"
        fix.return_value = None
        nar.return_value = ""
        out = await build_rejection_for_call(
            call_id="no-db-call",
            customer_slug=None,
            supplier=None,
            sales_agent=None,
            failing_checkpoints=failing,
        )
    assert out["category"] == "ADMIN_ERROR"
    # No db → no traces written; nothing to assert beyond no exception.
