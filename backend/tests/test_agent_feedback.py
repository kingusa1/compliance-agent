import json
from unittest.mock import AsyncMock, patch

import pytest

from app.agent.feedback import abstract_and_store_review
from app.models import AgentLearning


@pytest.mark.asyncio
async def test_abstract_and_store_creates_learning(test_db):
    fake_llm_response = json.dumps({
        "pattern": "agent asked DOB without waiting for explicit yes",
        "lesson": "customer_yes checkpoints require a clear verbal yes, not trailing silence",
    })

    with patch("app.agent.feedback._call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = fake_llm_response
        await abstract_and_store_review(
            db=test_db,
            supplier="E.ON Next",
            checkpoint_name="Agent confirms DOB",
            transcript_excerpt="Agent: DOB is 14th March? Customer: (silence)",
            agent_verdict="pass",
            human_verdict="fail",
            reviewer_notes="agent rushed past without confirmation",
        )

    rows = test_db.query(AgentLearning).all()
    assert len(rows) == 1
    assert rows[0].supplier == "E.ON Next"
    assert rows[0].checkpoint_name == "Agent confirms DOB"
    assert rows[0].agent_verdict == "pass"
    assert rows[0].human_verdict == "fail"
    assert "without waiting" in rows[0].pattern
    assert "verbal yes" in rows[0].lesson


@pytest.mark.asyncio
async def test_abstract_no_store_when_agent_and_human_agree(test_db):
    """If human confirmed agent's verdict, there's no lesson to learn — don't store."""
    with patch("app.agent.feedback._call_llm", new_callable=AsyncMock) as mock_llm:
        await abstract_and_store_review(
            db=test_db,
            supplier="E.ON Next",
            checkpoint_name="CP",
            transcript_excerpt="...",
            agent_verdict="pass",
            human_verdict="pass",
            reviewer_notes=None,
        )

    assert mock_llm.await_count == 0
    assert test_db.query(AgentLearning).count() == 0


@pytest.mark.asyncio
async def test_abstract_handles_llm_failure_gracefully(test_db):
    with patch("app.agent.feedback._call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.side_effect = Exception("llm unreachable")
        await abstract_and_store_review(
            db=test_db,
            supplier="E.ON Next",
            checkpoint_name="CP",
            transcript_excerpt="agent excerpt",
            agent_verdict="pass",
            human_verdict="fail",
            reviewer_notes="wrong",
        )

    assert test_db.query(AgentLearning).count() == 0


# 2026-05-27 wave-18 regression tests — off-loop DB write contract


@pytest.mark.asyncio
async def test_db_write_runs_off_event_loop(test_db):
    """Wave-18 contract: the persist step uses `asyncio.to_thread`, so the
    DB INSERT executes on a worker thread, not the asyncio loop. We assert
    by verifying that the threading.get_ident() captured inside the writer
    differs from the loop-thread's get_ident()."""
    import asyncio
    import threading

    loop_thread_id = threading.get_ident()
    write_thread_id: dict[str, int] = {}

    # Patch SessionLocal so we can record which thread the writer ran on.
    real_sl = None
    from app.database import SessionLocal as _RealSL

    class _SpySL:
        def __init__(self):
            write_thread_id["tid"] = threading.get_ident()
            self._real = _RealSL()

        def __getattr__(self, name):
            return getattr(self._real, name)

    fake_llm_response = json.dumps({
        "pattern": "off-loop write smoke",
        "lesson": "the persist call must run on a worker thread",
    })

    with patch("app.agent.feedback._call_llm", new_callable=AsyncMock) as mock_llm, \
         patch("app.database.SessionLocal", _SpySL):
        mock_llm.return_value = fake_llm_response
        await abstract_and_store_review(
            db=test_db,
            supplier="E.ON Next",
            checkpoint_name="off-loop check",
            transcript_excerpt="excerpt",
            agent_verdict="pass",
            human_verdict="fail",
            reviewer_notes="threading contract",
        )

    assert "tid" in write_thread_id, "writer never opened a SessionLocal"
    assert write_thread_id["tid"] != loop_thread_id, (
        f"Wave-18 regression: persist must run on a worker thread, but "
        f"ran on the asyncio loop thread (tid={write_thread_id['tid']}). "
        f"This is the bug that caused the 184s loop_lag freeze on "
        f"2026-05-27 11:05 UTC."
    )
    # Per python-reviewer HIGH — the threading assertion alone could
    # pass even if the worker skipped the actual write. Verify the row
    # landed in the DB so a future refactor that early-returns inside
    # the worker still gets caught.
    rows = test_db.query(AgentLearning).all()
    assert len(rows) == 1, (
        "Wave-18 regression: threading check passed but no AgentLearning "
        "row was persisted — the worker may have early-exited"
    )


@pytest.mark.asyncio
async def test_db_kwarg_accepted_but_ignored(test_db):
    """Wave-18 makes the `db` kwarg back-compat-only — the writer opens
    its own SessionLocal. Verify a NULL `db` doesn't crash the function
    (previously would have AttributeError'd on None.add)."""
    fake_llm_response = json.dumps({
        "pattern": "p",
        "lesson": "l",
    })
    with patch("app.agent.feedback._call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = fake_llm_response
        # `db=None` would previously crash — wave-18 ignores it.
        await abstract_and_store_review(
            db=None,
            supplier="EON",
            checkpoint_name="cp",
            transcript_excerpt="ex",
            agent_verdict="pass",
            human_verdict="fail",
            reviewer_notes=None,
        )
    # AgentLearning row was written via the worker's SessionLocal which
    # binds to the same in-memory SQLite engine the test fixture uses.
    assert test_db.query(AgentLearning).count() >= 1


@pytest.mark.asyncio
async def test_persist_failure_swallowed_does_not_crash_caller(test_db):
    """If the off-loop DB write itself raises (e.g. unique violation,
    connection drop), the exception is swallowed and the coroutine
    returns cleanly — never crashes the upstream review endpoint."""
    fake_llm_response = json.dumps({
        "pattern": "p",
        "lesson": "l",
    })

    class _BrokenSession:
        def add(self, *_a, **_kw):
            raise RuntimeError("simulated DB outage during off-loop write")
        def commit(self):
            raise RuntimeError("simulated DB outage during off-loop write")
        def rollback(self):
            pass
        def close(self):
            pass

    with patch("app.agent.feedback._call_llm", new_callable=AsyncMock) as mock_llm, \
         patch("app.database.SessionLocal", lambda: _BrokenSession()):
        mock_llm.return_value = fake_llm_response
        # Should NOT raise — the worker-thread try/except + caller-side
        # logging together must make this a quiet no-op.
        await abstract_and_store_review(
            db=test_db,
            supplier="EON",
            checkpoint_name="cp",
            transcript_excerpt="ex",
            agent_verdict="pass",
            human_verdict="fail",
            reviewer_notes=None,
        )
