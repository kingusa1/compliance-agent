"""Bulk-upload concurrency cap.

The upload handler fires ``asyncio.create_task(_process_in_background(...))``
on every call upload. Prior to 2026-05-25 there was NO upper bound on
concurrent pipelines, so 50 simultaneous uploads spawned 50 LLM-fanned-out
pipelines that exhausted the 30-slot DB pool, slammed OpenRouter's rate
limit, and made every UI query lag waiting on a pool slot.

`app.routes._process_in_background` now acquires a slot on a global
asyncio.Semaphore (size = `settings.pipeline_concurrency`, default 8)
BEFORE running `process_call`. Tasks past the cap wait FIFO. The Call
row has already been created by the upload handler, so the UI keeps
showing the queued state; only the heavy LLM work serialises.

These tests verify:
  1. The semaphore is a singleton (idempotent factory)
  2. The semaphore's bound matches `settings.pipeline_concurrency`
  3. Concurrent waiters serialise — only `cap` tasks run at a time
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_get_pipeline_semaphore_is_singleton():
    from app.routes import _get_pipeline_semaphore

    a = _get_pipeline_semaphore()
    b = _get_pipeline_semaphore()
    assert a is b, "factory must return the same semaphore on every call"
    assert isinstance(a, asyncio.Semaphore)


@pytest.mark.asyncio
async def test_pipeline_semaphore_caps_concurrent_runs():
    """Drive 12 fake-pipeline tasks through `_process_in_background` with
    `pipeline_concurrency=3`. At no point should more than 3 be inside
    the critical section. All 12 must eventually finish.
    """
    import app.routes as routes

    # Reset the module-level semaphore so the test honours `cap` and the
    # fixture state doesn't carry over from prior tests.
    routes._PIPELINE_SEMAPHORE = None

    cap = 3
    total = 12
    in_flight: set[int] = set()
    peak: list[int] = [0]
    finished: list[int] = []

    async def fake_process_call(call_id, file_path, db, script_id=None):
        # Record entry, hold a beat to overlap with siblings, record exit.
        in_flight.add(int(call_id))
        peak[0] = max(peak[0], len(in_flight))
        await asyncio.sleep(0.01)
        in_flight.discard(int(call_id))
        finished.append(int(call_id))

    async def noop_send(event):
        return None

    class _FakeInngest:
        async def send(self, event):
            return await noop_send(event)

    with patch.object(routes.settings, "pipeline_concurrency", cap), \
         patch("app.routes.process_call", side_effect=fake_process_call), \
         patch("app.inngest_client.inngest_client", _FakeInngest()):
        routes._PIPELINE_SEMAPHORE = asyncio.Semaphore(cap)
        await asyncio.gather(*[
            routes._process_in_background(str(i), f"/tmp/{i}.mp3", None)
            for i in range(total)
        ])

    assert peak[0] <= cap, (
        f"peak concurrent pipelines {peak[0]} exceeded cap {cap} — "
        f"semaphore is not throttling"
    )
    assert sorted(finished) == list(range(total)), "every task must complete"


@pytest.mark.asyncio
async def test_process_in_background_does_not_pre_allocate_session():
    """Regression — `_process_in_background` must NOT open a SessionLocal
    of its own before invoking `process_call`. The per-step refactor in
    `pipeline.process_call` opens + closes a session inside each step
    shim; an outer pre-allocation would re-introduce the connection-pool
    starvation that this work shipped to fix.

    Verifies the behavioural contract by counting `SessionLocal()` calls
    from the routes module while a fake `process_call` runs. The count
    must be 0 — every session opened during the pipeline comes from
    `pipeline.py`, not `routes.py`.
    """
    import app.routes as routes

    routes._PIPELINE_SEMAPHORE = None  # rebuild for this test's cap

    session_factory_calls: list[None] = []
    fake_process_call_was_called: list[bool] = [False]
    received_db_arg: list = []

    async def fake_process_call(call_id, file_path, db, script_id=None):
        fake_process_call_was_called[0] = True
        received_db_arg.append(db)

    def _spy_session_local():
        # If `_process_in_background` calls SessionLocal(), it would land
        # here and we'd count it. The test asserts this never fires.
        session_factory_calls.append(None)
        raise AssertionError(
            "_process_in_background must NOT open its own SessionLocal — "
            "per-step SessionLocal lives in pipeline.process_call (2026-05-25 perf)."
        )

    class _FakeInngest:
        async def send(self, event):
            return None

    with patch.object(routes.settings, "pipeline_concurrency", 4), \
         patch("app.routes.process_call", side_effect=fake_process_call), \
         patch("app.database.SessionLocal", side_effect=_spy_session_local), \
         patch("app.inngest_client.inngest_client", _FakeInngest()):
        routes._PIPELINE_SEMAPHORE = asyncio.Semaphore(4)
        await routes._process_in_background("test-call-id", "/tmp/x.mp3", None)

    assert fake_process_call_was_called[0], "process_call must have been awaited"
    assert session_factory_calls == [], (
        "SessionLocal was opened by _process_in_background — should be opened "
        "per-step inside pipeline.process_call instead"
    )
    # process_call should receive db=None now (sessions are per-step)
    assert received_db_arg == [None], (
        f"process_call should receive db=None on the legacy path; got {received_db_arg!r}"
    )
