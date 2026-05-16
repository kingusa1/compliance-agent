"""Durable pipeline (Phase D.2 — six-step wrap).

Listens for `call/uploaded` events and delegates to six pipeline step
functions, each wrapped in a `ctx.step.run(...)`. The boundary list mirrors
`app.pipeline._step_*` exactly:

    1. download_audio
    2. transcribe
    3. detect_metadata
    4. analyze_checkpoints
    5. score
    6. finalize

Each step is checkpointed independently — Inngest memoizes the return value
of a successful step on the (function_id, step_name, input_hash) tuple, so a
retry after a crash replays from the last successful step boundary rather
than re-doing transcription. Errors raised inside a step trigger Inngest's
exponential-backoff retry; after retries exhaust the run is marked failed
and visible in the Inngest dashboard at :8288 (and, once D04 lands, in
/observability).

Idempotency: step 4 (analyze_checkpoints) is the only step that creates
child rows (CallCheckpoint). The pipeline-level implementation does a
delete-then-insert under one DB transaction so a retried step never
double-writes.

We pass only small values (call_id, audio_path, source name) between steps —
heavy state (transcript, word_data, analysis) lives on the Call row and each
step re-reads what it needs. This keeps the (step_name, input_hash) pair
stable across retries and avoids huge memoized payloads in Inngest's state.

Each step emits a structured log line at start and end so the observability
layer can show progress:
    WORKFLOW_STEP step=<name> call_id=<id> status=<start|ok|err> duration_ms=<n>

This function runs ONLY when settings.use_inngest_pipeline is True AND the
upload route emits the event. With the flag on, the legacy asyncio task in
routes._process_in_background is SKIPPED (single-writer guarantees idempotent
results in the demo path). With the flag off, the legacy task runs and this
function never fires.
"""
from __future__ import annotations

import asyncio
import inspect
import time
from datetime import datetime
from app._clock import utcnow

import inngest

from app.inngest_client import inngest_client
from app.logger import log as app_log
from app.observability_metrics import record_pipeline_step
from app.workflows.events import CALL_REANALYZE, CALL_UPLOADED


# Per-step soft timeouts (seconds). Tuned to ~1.75× empirical p99 of each
# step on a 60-min call so a hung provider trips locally before Inngest's
# 7-min stuck-watchdog cron picks it up. Order mirrors the pipeline.
_STEP_TIMEOUTS: dict[str, int] = {
    "download_audio": 120,
    "transcribe": 300,
    "detect_metadata": 60,
    "analyze_checkpoints": 420,
    "score": 60,
    "finalize": 30,
}


def _write_step_error(call_id: str, step_name: str, err_msg: str) -> None:
    """Persist last_step_error on the Call row in its own short-lived
    SessionLocal so a partial step failure leaves a forensic breadcrumb the
    /observability/stuck page can surface. Best-effort — swallow DB errors
    to avoid masking the original exception the caller is about to re-raise.
    """
    try:
        from app.database import SessionLocal
        from app.models import Call

        db = SessionLocal()
        try:
            call = db.query(Call).filter_by(id=call_id).first()
            if call is not None:
                # Columns may not yet exist on older deployments; setattr
                # silently ignores absent attributes on raw model objects.
                if hasattr(call, "last_step_error"):
                    call.last_step_error = err_msg[:1000]
                db.commit()
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001
        app_log.warning(f"WORKFLOW_STEP step={step_name} call_id={call_id} write_error_failed={e!r}")


def _mark_step_started(call_id: str, step_name: str) -> None:
    """Set last_step_started_at + last_step_name + clear last_step_error
    on the Call row. Best-effort — same rationale as _write_step_error.
    """
    try:
        from app.database import SessionLocal
        from app.models import Call

        db = SessionLocal()
        try:
            call = db.query(Call).filter_by(id=call_id).first()
            if call is not None:
                if hasattr(call, "last_step_started_at"):
                    call.last_step_started_at = utcnow()
                if hasattr(call, "last_step_name"):
                    call.last_step_name = step_name
                if hasattr(call, "last_step_error"):
                    call.last_step_error = None
                db.commit()
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001
        app_log.warning(f"WORKFLOW_STEP step={step_name} call_id={call_id} mark_started_failed={e!r}")


@inngest_client.create_function(
    fn_id="process-call",
    trigger=inngest.TriggerEvent(event=CALL_UPLOADED),
    retries=5,
)
async def process_call(ctx: inngest.Context) -> dict:
    data = ctx.event.data or {}
    call_id = data.get("call_id")
    audio_path = data.get("audio_path")
    script_id = data.get("script_id")
    if not call_id or not audio_path:
        raise RuntimeError(f"process-call missing required fields: {data!r}")

    app_log.info(f"INNGEST_PROCESS_CALL_START call_id={call_id}")

    _t0 = time.monotonic()
    try:
        audio_path_local = await ctx.step.run(
            "download_audio",
            _logged_step(call_id, "download_audio", _do_download_audio),
            call_id, audio_path,
        )
    finally:
        record_pipeline_step("download_audio", time.monotonic() - _t0)

    _t0 = time.monotonic()
    try:
        transcribe_out = await ctx.step.run(
            "transcribe",
            _logged_step(call_id, "transcribe", _do_transcribe),
            call_id, audio_path_local,
        )
    finally:
        record_pipeline_step("transcribe", time.monotonic() - _t0)

    _t0 = time.monotonic()
    try:
        await ctx.step.run(
            "detect_metadata",
            _logged_step(call_id, "detect_metadata", _do_detect_metadata),
            call_id, script_id,
        )
    finally:
        record_pipeline_step("detect_metadata", time.monotonic() - _t0)

    _t0 = time.monotonic()
    try:
        analysis = await ctx.step.run(
            "analyze_checkpoints",
            _logged_step(call_id, "analyze_checkpoints", _do_analyze_checkpoints),
            call_id,
        )
    finally:
        record_pipeline_step("analyze_checkpoints", time.monotonic() - _t0)

    _t0 = time.monotonic()
    try:
        score_out = await ctx.step.run(
            "score",
            _logged_step(call_id, "score", _do_score),
            call_id, analysis,
        )
    finally:
        record_pipeline_step("score", time.monotonic() - _t0)

    _t0 = time.monotonic()
    try:
        finalize_out = await ctx.step.run(
            "finalize",
            _logged_step(call_id, "finalize", _do_finalize),
            call_id,
        )
    finally:
        record_pipeline_step("finalize", time.monotonic() - _t0)

    # B-2: emit call/finalized so the L6 RAG ingest function (rag_ingest_call_fn)
    # picks up the now-finalized transcript and writes transcript_chunks.
    # Without this, embeddings_available=false on /api/rag/search forever.
    # Using ctx.step.send_event keeps the emit inside the durable run so a
    # crash before send is retried by Inngest, not silently dropped.
    try:
        await ctx.step.send_event(
            "emit_call_finalized",
            inngest.Event(
                name="call/finalized",
                data={"call_id": call_id},
            ),
        )
        app_log.info(f"INNGEST_EVENT_SENT name=call/finalized call_id={call_id}")
    except Exception as e:  # noqa: BLE001
        app_log.warning(f"INNGEST_EVENT_FAILED name=call/finalized call_id={call_id} err={e!r}")

    app_log.info(
        f"INNGEST_PROCESS_CALL_DONE call_id={call_id} "
        f"status={score_out.get('status')} score={score_out.get('score')} "
        f"compliance_status={finalize_out.get('compliance_status')} "
        f"transcribe_source={transcribe_out.get('source')}"
    )
    return {
        "call_id": call_id,
        "status": score_out.get("status"),
        "score": score_out.get("score"),
        "compliance_status": finalize_out.get("compliance_status"),
    }


# ── shared logged-step wrapper ───────────────────────────────────────────
_STEP_LOG_MAX_BYTES = 64_000  # truncate payload JSON beyond this — bounds row size


def _truncate_for_log(value):
    """Best-effort small-JSON-friendly representation of an arbitrary value.

    The pipeline passes simple things (str, int, dicts of those) between
    steps so JSON-serialise cleanly; for anything else (bytes, custom
    objects, etc) fall back to repr() truncated to _STEP_LOG_MAX_BYTES
    so we never explode the row size or fail to insert.
    """
    import json
    try:
        s = json.dumps(value, default=str)
        if len(s) > _STEP_LOG_MAX_BYTES:
            s = s[:_STEP_LOG_MAX_BYTES] + "…[truncated]"
        return json.loads(s) if s.startswith(("{", "[", '"')) else s
    except Exception:
        try:
            r = repr(value)
            return r[:_STEP_LOG_MAX_BYTES] + ("…[truncated]" if len(r) > _STEP_LOG_MAX_BYTES else "")
        except Exception:
            return None


def _persist_step_running(call_id: str, step_name: str, payload_in_args, payload_in_kwargs):
    """Insert a pipeline_step_log row when the step starts. Returns the row id
    (or None on best-effort failure) so _persist_step_done can update it.
    """
    try:
        import uuid as _uuid
        from datetime import datetime as _dt
        from app.database import SessionLocal
        from app.models import PipelineStepLog

        row_id = str(_uuid.uuid4())
        # Strip the call_id / audio_path positional spam — caller-friendly view.
        capture_in = {
            "args": [_truncate_for_log(a) for a in payload_in_args],
            "kwargs": {k: _truncate_for_log(v) for k, v in payload_in_kwargs.items()},
        }
        db = SessionLocal()
        try:
            db.add(PipelineStepLog(
                id=row_id,
                call_id=call_id,
                step_name=step_name,
                status="running",
                payload_in=capture_in,
                started_at=_dt.utcnow(),
            ))
            db.commit()
            return row_id
        finally:
            db.close()
    except Exception as e:
        app_log.warning(f"WORKFLOW_STEP step={step_name} call_id={call_id} step_log_start_failed={e!r}")
        return None


def _persist_step_done(row_id, step_name: str, status: str, payload_out, error_message, duration_ms: int):
    """Update the running pipeline_step_log row to ok | err with output JSON."""
    if not row_id:
        return
    try:
        from datetime import datetime as _dt
        from app.database import SessionLocal
        from app.models import PipelineStepLog

        db = SessionLocal()
        try:
            row = db.query(PipelineStepLog).filter_by(id=row_id).first()
            if row is None:
                return
            row.status = status
            row.payload_out = _truncate_for_log(payload_out) if payload_out is not None else None
            row.error_message = error_message
            row.ended_at = _dt.utcnow()
            row.duration_ms = duration_ms
            db.commit()
        finally:
            db.close()
    except Exception as e:
        app_log.warning(f"WORKFLOW_STEP step={step_name} step_log_done_failed={e!r}")


def _logged_step(call_id: str, step_name: str, fn):
    """Return an async wrapper around `fn` that emits WORKFLOW_STEP logs at
    start / ok / err, marks the Call row with last_step_started_at +
    last_step_name (so the watchdog can detect stuck steps), persists a
    pipeline_step_log row with input + output JSON for the live
    /observability flow viz, and enforces a per-step asyncio.wait_for
    timeout from `_STEP_TIMEOUTS`. Sync inner functions are bounced
    through a thread executor so the timeout actually interrupts I/O-bound
    work.
    """
    timeout_s = _STEP_TIMEOUTS.get(step_name)

    async def _wrapped(*args, **kwargs):
        started = time.time()
        _mark_step_started(call_id, step_name)
        log_row_id = _persist_step_running(call_id, step_name, args, kwargs)
        app_log.info(f"WORKFLOW_STEP step={step_name} call_id={call_id} status=start duration_ms=0")
        try:
            raw = fn(*args, **kwargs)
            if inspect.isawaitable(raw):
                coro = raw
            else:
                # Sync return — wrap in a coroutine so wait_for can cancel it.
                async def _sync_wrap():
                    return raw
                coro = _sync_wrap()
            if timeout_s is not None:
                result = await asyncio.wait_for(coro, timeout=timeout_s)
            else:
                result = await coro
            elapsed_ms = int((time.time() - started) * 1000)
            app_log.info(f"WORKFLOW_STEP step={step_name} call_id={call_id} status=ok duration_ms={elapsed_ms}")
            _persist_step_done(log_row_id, step_name, "ok", result, None, elapsed_ms)
            return result
        except asyncio.TimeoutError:
            elapsed_ms = int((time.time() - started) * 1000)
            err_msg = f"timed out after {timeout_s}s (step={step_name})"
            app_log.error(
                f"WORKFLOW_STEP step={step_name} call_id={call_id} status=err duration_ms={elapsed_ms} err=TimeoutError({err_msg!r})"
            )
            _write_step_error(call_id, step_name, err_msg)
            _persist_step_done(log_row_id, step_name, "err", None, err_msg, elapsed_ms)
            raise
        except Exception as e:
            elapsed_ms = int((time.time() - started) * 1000)
            app_log.error(
                f"WORKFLOW_STEP step={step_name} call_id={call_id} status=err duration_ms={elapsed_ms} err={e!r}"
            )
            _write_step_error(call_id, step_name, repr(e))
            _persist_step_done(log_row_id, step_name, "err", None, repr(e), elapsed_ms)
            raise

    return _wrapped


# ── per-step async shims (own DB session each) ───────────────────────────

async def _do_download_audio(call_id: str, file_path: str) -> str:
    from app.database import SessionLocal
    from app.pipeline import _step_download_audio

    db = SessionLocal()
    try:
        audio_path, _local = await _step_download_audio(call_id, file_path, db)
        # NOTE: temp file (if any) is left for the next idle sweep / restart;
        # acceptable for D02. Revisit if disk pressure surfaces.
        return audio_path
    finally:
        db.close()


async def _do_transcribe(call_id: str, audio_path: str) -> dict:
    from app.database import SessionLocal
    from app.pipeline import _step_transcribe

    db = SessionLocal()
    try:
        result = await _step_transcribe(call_id, audio_path, db)
        # Strip the heavy 'transcript' field from the memoized return value —
        # it's already on the Call row, no need to round-trip it through
        # Inngest state. Source is kept for diagnostics.
        return {"source": result.get("source")}
    finally:
        db.close()


async def _do_detect_metadata(call_id: str, script_id: str | None) -> dict:
    from app.database import SessionLocal
    from app.models import Call
    from app.pipeline import _step_detect_metadata

    db = SessionLocal()
    try:
        call = db.query(Call).filter_by(id=call_id).first()
        if not call:
            raise RuntimeError(f"detect_metadata: call {call_id} not found")
        transcript_data = {"transcript": call.transcript or "", "source": "from_db"}
        await _step_detect_metadata(call_id, transcript_data, db, script_id)
        return {"call_id": call_id}
    finally:
        db.close()


async def _do_analyze_checkpoints(call_id: str) -> dict:
    from app.database import SessionLocal
    from app.models import Call
    from app.pipeline import _step_analyze_checkpoints

    db = SessionLocal()
    try:
        call = db.query(Call).filter_by(id=call_id).first()
        if not call:
            raise RuntimeError(f"analyze_checkpoints: call {call_id} not found")
        transcript_data = {"transcript": call.transcript or "", "source": "from_db"}
        analysis = await _step_analyze_checkpoints(call_id, transcript_data, db)
        # Trim the fallback path's nested object so it survives JSON
        # serialization for Inngest memoization. The script-mode return is
        # already JSON-clean.
        if analysis.get("mode") == "no_script_match":
            v1 = analysis.pop("v1")
            analysis["fallback_summary"] = {
                "agent_name": v1.agent_name,
                "customer_name": v1.customer_name,
                "compliant": bool(v1.compliant),
                "checkpoint_count": len(v1.checkpoints) if v1.checkpoints else 0,
            }
        return analysis
    finally:
        db.close()


async def _do_score(call_id: str, analysis: dict) -> dict:
    """Score is currently async-shimmed even though the underlying step is
    sync — it lets the workflow handler stay uniform and lets us await any
    future I/O the score step grows.

    2026-05-15 compliance contract: rejections are **reviewer-initiated
    only**. The Inngest path no longer auto-creates a Rejection row from
    the AI verdict; AI hints surface on the awaiting-review row in
    /tracker but the call stays out of the /rejections tab until a human
    submits a FAIL/REVIEW verdict via /api/verdict (which calls
    ``auto_create_rejection_for_verdict`` from hitl_routes.submit_verdict).
    Mirrors the asyncio ``app.pipeline.process_call`` path which already
    dropped its ``_maybe_create_rejection`` call during the 2026-05-12
    taxonomy rebuild.
    """
    from app.database import SessionLocal
    from app.models import Call
    from app.pipeline import _step_score

    db = SessionLocal()
    try:
        if analysis.get("mode") == "no_script_match":
            # The original v1 object got dropped at the analyze boundary
            # so _step_score (which reads analysis["v1"]) can't run here.
            # The fallback path now persists score/compliant/reason on
            # the call row directly inside analyze, so this score step
            # is a status-only pass-through.
            call = db.query(Call).filter_by(id=call_id).first()
            if not call:
                raise RuntimeError(f"score: call {call_id} not found")
            if call.status != "needs_manual_review":
                call.status = "completed"
            db.commit()
            return {
                "score": call.score,
                "compliant": call.compliant,
                "status": call.status,
                "reason": call.reason,
            }
        result = _step_score(call_id, analysis, db)
        return result
    finally:
        db.close()


async def _do_finalize(call_id: str) -> dict:
    from app.database import SessionLocal
    from app.pipeline import _step_finalize

    db = SessionLocal()
    try:
        return _step_finalize(call_id, db)
    finally:
        db.close()


@inngest_client.create_function(
    fn_id="process-call-reanalyze",
    trigger=inngest.TriggerEvent(event=CALL_REANALYZE),
    retries=3,
)
async def process_call_reanalyze(ctx: inngest.Context) -> dict:
    """Replay sub-pipeline for `call/reanalyze`. Skips audio download +
    transcription. Steps 4-5-6 only (analyze_checkpoints → score →
    finalize). Existing CallCheckpoint idempotency replaces prior rows
    via delete-and-insert by call_id + checkpoint_index."""
    data = ctx.event.data or {}
    call_id = data.get("call_id")
    if not call_id:
        raise RuntimeError(f"process-call-reanalyze missing call_id: {data!r}")

    app_log.info(f"INNGEST_REANALYZE_START call_id={call_id}")

    _t0 = time.monotonic()
    try:
        analysis = await ctx.step.run(
            "analyze_checkpoints",
            _logged_step(call_id, "analyze_checkpoints", _do_analyze_checkpoints),
            call_id,
        )
    finally:
        record_pipeline_step("analyze_checkpoints", time.monotonic() - _t0)

    _t0 = time.monotonic()
    try:
        score_out = await ctx.step.run(
            "score",
            _logged_step(call_id, "score", _do_score),
            call_id, analysis,
        )
    finally:
        record_pipeline_step("score", time.monotonic() - _t0)

    _t0 = time.monotonic()
    try:
        finalize_out = await ctx.step.run(
            "finalize",
            _logged_step(call_id, "finalize", _do_finalize),
            call_id,
        )
    finally:
        record_pipeline_step("finalize", time.monotonic() - _t0)

    app_log.info(
        f"INNGEST_REANALYZE_DONE call_id={call_id} "
        f"status={score_out.get('status')} score={score_out.get('score')} "
        f"compliance_status={finalize_out.get('compliance_status')}"
    )
    return {
        "call_id": call_id,
        "status": score_out.get("status"),
        "score": score_out.get("score"),
        "compliance_status": finalize_out.get("compliance_status"),
    }
