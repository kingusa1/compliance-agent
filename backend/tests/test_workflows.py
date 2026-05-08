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

    h = _ListHandler(level=logging.INFO)
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
        assert starts, f"expected start log, got: {messages}"
        assert oks, f"expected ok log, got: {messages}"
    finally:
        compliance_logger.removeHandler(h)


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


def test_analyze_checkpoints_step_is_idempotent(test_db, monkeypatch):
    """Running the analyze step twice on the same call_id must not double the
    CallCheckpoint row count. Uses the v1-fallback path (no Script row exists)
    with the v1 analyzer monkeypatched to return canned checkpoints.
    """
    from app import pipeline as pipeline_module

    call = Call(
        id="call-idem-test",
        filename="silent.wav",
        file_path="/tmp/does-not-matter.wav",
        status="processing",
        transcript="hello world",
    )
    test_db.add(call)
    test_db.commit()

    class _FakeCheckpoint:
        def __init__(self, rule, passed, excerpt):
            self.rule = rule
            self.passed = passed
            self.excerpt = excerpt

    class _FakeV1:
        def __init__(self):
            self.agent_name = "Sarah"
            self.customer_name = "John"
            self.excerpt = "evidence"
            self.compliant = True
            self.reason = "ok"
            self.checkpoints = [
                _FakeCheckpoint("rule-1", True, "ev-1"),
                _FakeCheckpoint("rule-2", False, "ev-2"),
                _FakeCheckpoint("rule-3", True, "ev-3"),
            ]

    async def _fake_v1(transcript):
        return _FakeV1()

    monkeypatch.setattr(pipeline_module, "analyze_compliance_v1", _fake_v1)

    transcript_data = {"transcript": "hello world", "source": "test"}

    # First run — expect 3 CallCheckpoint rows
    asyncio.run(pipeline_module._step_analyze_checkpoints("call-idem-test", transcript_data, test_db))
    first_count = test_db.query(CallCheckpoint).filter_by(call_id="call-idem-test").count()
    assert first_count == 3, f"expected 3 rows after first run, got {first_count}"

    # Second run on the same call_id — expect STILL 3 (not 6)
    asyncio.run(pipeline_module._step_analyze_checkpoints("call-idem-test", transcript_data, test_db))
    second_count = test_db.query(CallCheckpoint).filter_by(call_id="call-idem-test").count()
    assert second_count == 3, (
        f"idempotency violated: expected 3 rows after retry, got {second_count} "
        "— the delete-then-insert guard in _step_analyze_checkpoints is broken"
    )
