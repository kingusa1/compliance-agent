"""Replay path — re-derive a call's verdict from its stored transcript.

Cost model: zero re-transcription, zero new audio I/O. Pipeline steps 3
(detect_metadata, if script_id is null) → 4 (analyze_checkpoints) → 5
(score) → 6 (finalize) re-run.

2026-05-25 — the prior implementation emitted a `call/reanalyze` Inngest
event and returned 202. That event went NOWHERE in prod because
``USE_INNGEST_PIPELINE=false`` on Railway, so every Reanalyze click was a
silent no-op. The 422 gate was also too strict: when a call halted at
``needs_classification`` because the supplier wasn't detected on first
pass (script_id null), the user had no recovery — the UI showed the
transcript but the button refused to reanalyze.

The new behaviour:

  * Hard precondition: transcript + word_data must exist. These come from
    transcription, which Reanalyze does NOT re-run by design.
  * If ``script_id`` is null but the transcript exists, kick a fresh
    detect_metadata pass so the supplier+script can be inferred from the
    same text the reviewer is reading on the call detail page.
  * Run analyze → score → finalize SYNCHRONOUSLY in the request handler's
    asyncio task. Fast enough for the user to feel the button worked;
    the heavy phase (transcribe) was already paid at upload time.
  * Idempotent: existing CallCheckpoint rows are wiped by
    `_step_analyze_checkpoints`'s delete-then-insert before re-grading.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.logger import log
from app.models import Call


async def reanalyze(call_id: str, db: Session, actor_id: str | None = None) -> dict:
    call = db.query(Call).filter(Call.id == call_id).first()
    if call is None:
        raise HTTPException(status_code=404, detail=f"Call {call_id} not found")
    if not call.transcript or not call.word_data:
        raise HTTPException(
            status_code=422,
            detail="Call lacks transcript / word_data — cannot reanalyze.",
        )

    run_id = str(uuid.uuid4())

    # Audit BEFORE firing the task so the trail records intent even if the
    # async run errors out half-way. The actor_id is the authenticated
    # reviewer's id (routes.py derives it from `current_reviewer`).
    record_audit(
        db,
        action="reanalyze",
        entity_type="call",
        entity_id=call_id,
        payload={"run_id": run_id, "actor": actor_id, "had_script": bool(call.script_id)},
        actor_id=actor_id,
    )
    db.commit()

    log.info(
        f"REANALYZE start call_id={call_id} run_id={run_id} "
        f"actor={actor_id} had_script={bool(call.script_id)}"
    )

    # Fire-and-forget on the request's loop — same lifecycle as the
    # original upload's `_process_in_background`. The UI polls / SSEs the
    # call row to see new verdict appear; we don't block the HTTP response.
    asyncio.create_task(_run_reanalysis(call_id, run_id))

    return {"call_id": call_id, "run_id": run_id, "actor": actor_id}


async def _run_reanalysis(call_id: str, run_id: str) -> None:
    """Run detect_metadata (if needed) → analyze → score → finalize
    against the stored transcript using fresh DB sessions per step (same
    pattern as the legacy ``process_call`` orchestrator)."""
    from app.database import SessionLocal
    from app.pipeline import (
        _step_analyze_checkpoints,
        _step_classify_content,
        _step_detect_metadata,
        _step_finalize,
        _step_score,
        _trace_step,
    )

    try:
        # Step 3 (only if missing) — supplier + script + names.
        db = SessionLocal()
        try:
            call = db.query(Call).filter_by(id=call_id).first()
            if call is None:
                log.warning(f"REANALYZE call_id={call_id} run_id={run_id} disappeared")
                return
            transcript_data: dict[str, Any] = {
                "transcript": call.transcript or "",
                "source": "from_db",
            }
            needs_detect = not call.script_id
        finally:
            db.close()

        if needs_detect:
            db = SessionLocal()
            try:
                await _trace_step(
                    call_id,
                    "detect_metadata",
                    _step_detect_metadata,
                    call_id,
                    transcript_data,
                    db,
                    None,  # script_id — unknown, that's why we're detecting
                )
            finally:
                db.close()

        # Step 3.5 — content classifier (per-segment routing for the
        # taxonomy rebuild). Must run BEFORE analyze so each segment knows
        # its rubric.
        db = SessionLocal()
        try:
            classify_result = await _trace_step(
                call_id,
                "classify_content",
                _step_classify_content,
                call_id,
                transcript_data,
                db,
            )
        finally:
            db.close()

        if classify_result.get("halted"):
            log.warning(
                f"REANALYZE call_id={call_id} run_id={run_id} "
                f"halted=needs_classification — no segments detected"
            )
            return

        # Step 4 — analyze checkpoints (idempotent delete-then-insert).
        db = SessionLocal()
        try:
            analysis = await _trace_step(
                call_id,
                "analyze_checkpoints",
                _step_analyze_checkpoints,
                call_id,
                transcript_data,
                db,
            )
        finally:
            db.close()

        # Step 5 — derive score / compliant / status / reason / bucket.
        db = SessionLocal()
        try:
            await _trace_step(
                call_id, "score", _step_score, call_id, analysis, db
            )
        finally:
            db.close()

        # Step 6 — finalize: derive_compliance + completed_at + L2
        # extraction writer + post-extraction merge.
        db = SessionLocal()
        try:
            await _trace_step(
                call_id, "finalize", _step_finalize, call_id, db
            )
        finally:
            db.close()

        log.info(f"REANALYZE done call_id={call_id} run_id={run_id}")
    except Exception as e:  # noqa: BLE001 — terminal log + status flip
        log.error(f"REANALYZE failed call_id={call_id} run_id={run_id} err={e!r}")
        # Use a NEW local name so we never alias an unbound `db` from the
        # try-block — if the exception fired before the first SessionLocal()
        # call (e.g. ImportError on the from-app.pipeline line), referencing
        # `db` here would raise UnboundLocalError and mask the original
        # exception entirely. The error session is independent and always
        # opened fresh.
        _err_db = SessionLocal()
        try:
            call = _err_db.query(Call).filter_by(id=call_id).first()
            if call is not None:
                # Don't mask a prior 'completed' state — only flip if
                # we'd otherwise leave the row in a half-processed state.
                if call.status not in ("completed", "needs_manual_review"):
                    call.status = "failed"
                    call.reason = (call.reason or "") + f" | reanalyze error: {e!r}"[:240]
                    _err_db.commit()
        finally:
            _err_db.close()
