"""Tests for the durable Inngest workflow at app.workflows.process_call.

Three behaviors we care about for D02:

1. The workflow has the 6 expected ctx.step.run boundaries in the right order.
   This is a structural assertion against the source — cheap, deterministic,
   catches accidental step removal during future refactors.

2. The analyze_checkpoints step is idempotent — running it twice on the same
   call_id produces the SAME number of CallCheckpoint rows (delete-then-insert
   guard). The contract claims this; this test enforces it.

3. _logged_step (the wrapper that produces the async handler each
   ctx.step.run boundary receives) emits the WORKFLOW_STEP=ok structured log
   on success and =err on failure, so the future /observability page
   (D03+D04) and grep-based gates have reliable signal.

We intentionally do NOT exercise the real Inngest dev server here — that path
is covered by D02's gate verification in `.planning/durability-tasks/D02-...json`.
This test stays a unit test so CI doesn't need an Inngest sidecar.
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

import pytest

from app.models import Call, CallCheckpoint
from app.workflows.process_call import _logged_step


WORKFLOW_SRC = Path(__file__).resolve().parent.parent / "app" / "workflows" / "process_call.py"


def test_workflow_has_six_step_run_boundaries_in_order():
    """The contract requires 6 named steps in this exact order in `process_call`.
    Reading them straight from the source guards against silent step removal
    in refactors. Wave 3 added a sibling `process_call_reanalyze` function with
    its own step.run boundaries (steps 4-5-6 only); we scope the search to
    `process_call` only by slicing at the next top-level function header.
    """
    src = WORKFLOW_SRC.read_text()
    # Slice from the first `async def process_call` to the next top-level
    # `async def `/`def ` so we only count boundaries inside process_call.
    start = src.index("async def process_call(")
    rest = src[start + len("async def process_call("):]
    next_def = re.search(r"\n(async def |def )", rest)
    end = (start + len("async def process_call(")) + (next_def.start() if next_def else len(rest))
    process_call_src = src[start:end]
    found = re.findall(r'ctx\.step\.run\(\s*"([^"]+)"', process_call_src)
    expected = [
        "download_audio",
        "transcribe",
        "detect_metadata",
        "analyze_checkpoints",
        "score",
        "finalize",
    ]
    assert found == expected, f"workflow step order drift: found={found} expected={expected}"


def test_logged_step_emits_workflow_step_logs_on_success(monkeypatch):
    """Wave 2 logger sets propagate=False so caplog can't capture via root.
    Temporarily flip propagate=True for the test, then use caplog-style
    capture by reading the named logger's records via a list handler."""
    from app.logger import log as compliance_logger
    captured: list[logging.LogRecord] = []

    class _ListHandler(logging.Handler):
        def emit(self, record): captured.append(record)

    # 2026-05-24: the start-of-step log was demoted to DEBUG in
    # workflows/process_call.py:_logged_step to stop saturating Railway's
    # 500 lines/s replica budget. The pipeline_step_log table row is
    # the load-bearing observability artefact for "this step began";
    # the log line is decorative. Capture at DEBUG so this test still
    # exercises both start + ok emit paths.
    h = _ListHandler(level=logging.DEBUG)
    prior_level = compliance_logger.level
    compliance_logger.setLevel(logging.DEBUG)
    compliance_logger.addHandler(h)
    try:
        async def _ok():
            return {"answer": 42}

        handler = _logged_step("call-test", "transcribe", _ok)
        result = asyncio.run(handler())
        assert result == {"answer": 42}

        messages = [r.getMessage() for r in captured]
        starts = [m for m in messages if "WORKFLOW_STEP step=transcribe" in m and "status=start" in m]
        oks = [m for m in messages if "WORKFLOW_STEP step=transcribe" in m and "status=ok" in m]
        assert starts, f"expected start log (DEBUG), got: {messages}"
        assert oks, f"expected ok log, got: {messages}"
    finally:
        compliance_logger.removeHandler(h)
        compliance_logger.setLevel(prior_level)


def test_logged_step_emits_err_log_and_reraises_on_failure(monkeypatch):
    from app.logger import log as compliance_logger
    captured: list[logging.LogRecord] = []

    class _ListHandler(logging.Handler):
        def emit(self, record): captured.append(record)

    h = _ListHandler(level=logging.ERROR)
    compliance_logger.addHandler(h)
    try:
        async def _boom():
            raise RuntimeError("kaboom")

        handler = _logged_step("call-test", "score", _boom)
        with pytest.raises(RuntimeError, match="kaboom"):
            asyncio.run(handler())

        err_logs = [r.getMessage() for r in captured
                    if "WORKFLOW_STEP step=score" in r.getMessage() and "status=err" in r.getMessage()]
        assert err_logs, "expected an err log line for the failing step"
    finally:
        compliance_logger.removeHandler(h)


def test_logged_step_handles_sync_callables_too(caplog):
    """Defensive: if a step shim is sync rather than async, _logged_step
    should still work (await result will skip the await on a non-awaitable).
    """
    caplog.set_level(logging.INFO, logger="compliance")

    def _sync_ok():
        return "hello"

    handler = _logged_step("call-test", "score", _sync_ok)
    result = asyncio.run(handler())
    assert result == "hello"


def test_analyze_checkpoints_step_wipes_existing_rows(test_db, monkeypatch):
    """Idempotency contract — 2026-05-12 taxonomy rebuild.

    Pre-existing CallCheckpoint rows for the call MUST be wiped before
    the segment loop runs, so reruns can't accumulate duplicates. We
    deliberately call the step against a call with NO CallSegment rows
    (which is the zero-segment halt path) so the test stays self-
    contained: the wipe happens at the very top of the step, before
    the loop, so it executes regardless of whether segments exist.
    """
    from app import pipeline as pipeline_module

    call = Call(
        id="call-wipe-test",
        filename="silent.wav",
        file_path="/tmp/does-not-matter.wav",
        status="processing",
        transcript="hello world",
        call_type="lead_gen",
    )
    test_db.add(call)
    test_db.flush()

    # Seed a stale CallCheckpoint row from a prior analyzer run.
    test_db.add(
        CallCheckpoint(
            call_id="call-wipe-test",
            rule_text="legacy-rule",
            passed=True,
            excerpt="legacy",
            confidence="high",
            needs_review=False,
        )
    )
    test_db.commit()
    assert (
        test_db.query(CallCheckpoint).filter_by(call_id="call-wipe-test").count() == 1
    )

    transcript_data = {"transcript": "hello world", "source": "test"}
    asyncio.run(
        pipeline_module._step_analyze_checkpoints(
            "call-wipe-test", transcript_data, test_db
        )
    )

    # The wipe at the top of the step must have cleared the legacy row.
    assert (
        test_db.query(CallCheckpoint).filter_by(call_id="call-wipe-test").count() == 0
    ), "_step_analyze_checkpoints did not wipe pre-existing CallCheckpoint rows"

    # Second run is still idempotent (no rows, no duplication).
    asyncio.run(
        pipeline_module._step_analyze_checkpoints(
            "call-wipe-test", transcript_data, test_db
        )
    )
    assert (
        test_db.query(CallCheckpoint).filter_by(call_id="call-wipe-test").count() == 0
    )
