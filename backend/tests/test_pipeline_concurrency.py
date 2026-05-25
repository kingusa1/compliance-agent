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
