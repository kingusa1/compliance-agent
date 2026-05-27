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

        # Step 7 (wave-21, 2026-05-27) — re-derive Call.transcript from
        # word_data using the current `_detect_agent_speaker` heuristic.
        # Closes the wave-16 gap where role tags were fixed in the live
        # `/api/calls/{id}/words` derivation but `Call.transcript` (which
        # /bundle, RAG ingest, exports, and AI prompts read directly)
        # still carried the OLD diarized labels at ingest time.
        #
        # The user reported on 2026-05-27 that even after Reanalyze, the
        # Elzicle Ltd call still showed swapped Agent/Customer labels in
        # the UI. Root cause: the persisted text was never re-derived.
        # Now it is, every Reanalyze click. Idempotent: writes only when
        # the new text differs.
        await _rederive_speaker_labels(call_id, run_id)

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


async def _rederive_speaker_labels(call_id: str, run_id: str) -> None:
    """Wave-21 (2026-05-27) — re-derive `Call.transcript` Agent/Customer
    labels from `Call.word_data` using the current `_detect_agent_speaker`
    heuristic (wave-16).

    Why this exists: wave-16 fixed the role-tagging logic in
    `app/transcription.py:_detect_agent_speaker`, but the persisted
    `Call.transcript` column was written at original ingest with the
    OLD heuristic. `/api/calls/{id}/words` re-derives at request time;
    `/api/calls/{id}/bundle` and any consumer reading `Call.transcript`
    directly (RAG, exports) saw stale labels. This helper closes the
    gap on the Reanalyze path.

    Off-loop via `asyncio.to_thread` per wave-18 pattern — the
    `format_diarized_transcript` + JSON parse + DB commit shouldn't
    block the asyncio loop while reviewers are mid-keystroke. Idempotent:
    writes only when the new text differs from `Call.transcript`. Never
    propagates an exception — the reviewer's Reanalyze must not fail
    just because the label re-derive hit a snag.

    Sources (per BRAIN/00_LAW_OF_ENTERPRISE_GRADE §0 — re-used from
    wave-18 research agent `a3b9b2dc48f2ca55d`):
      - Python asyncio docs (asyncio.to_thread for blocking sync work)
      - Wave-18 commit message (off-loop DB write pattern)
      - Wave-17 commit message (idempotent diff-only writer)
    """
    import asyncio
    import json as _json

    from app.database import SessionLocal as _SL
    from app.transcription import format_diarized_transcript

    def _persist_label_rederivation() -> str:
        """Sync worker: runs entirely on a threadpool thread, opens its
        own SessionLocal so neither the JSON parse nor the DB commit
        can block the asyncio loop. Returns a status string for logging.
        """
        _db = _SL()
        try:
            call = _db.query(Call).filter_by(id=call_id).first()
            if call is None:
                return "skipped:call_gone"
            if not call.word_data:
                return "skipped:no_word_data"
            try:
                raw = call.word_data
                words = (
                    _json.loads(raw)
                    if isinstance(raw, (str, bytes, bytearray))
                    else raw
                )
            except (_json.JSONDecodeError, ValueError, TypeError):
                return "skipped:word_data_corrupt"
            if not isinstance(words, list) or not words:
                return "skipped:empty_words"
            try:
                new_text = format_diarized_transcript(words)
            except Exception as fmt_e:  # noqa: BLE001
                log.warning(
                    "rederive_speaker_labels format failed call_id=%s: %s: %s",
                    call_id, type(fmt_e).__name__, fmt_e,
                )
                return "skipped:format_failed"
            if new_text == (call.transcript or ""):
                return "unchanged"
            call.transcript = new_text
            _db.commit()
            return "updated"
        except Exception as outer_e:  # noqa: BLE001 — never propagate
            try:
                _db.rollback()
            except Exception:  # noqa: BLE001
                pass
            log.warning(
                "rederive_speaker_labels failed call_id=%s: %s: %s",
                call_id, type(outer_e).__name__, outer_e,
            )
            return "skipped:exception"
        finally:
            try:
                _db.close()
            except Exception:  # noqa: BLE001
                pass

    status = await asyncio.to_thread(_persist_label_rederivation)
    if status == "updated":
        log.info(
            "REANALYZE speaker_labels rederived call_id=%s run_id=%s",
            call_id, run_id,
        )
    elif status != "unchanged":
        log.info(
            "REANALYZE speaker_labels not-rederived call_id=%s reason=%s",
            call_id, status,
        )
