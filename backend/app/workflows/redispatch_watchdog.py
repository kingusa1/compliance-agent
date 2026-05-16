"""Stuck-step watchdog (Pillar 1 — Durability).

Inngest scheduled function that runs once per minute, scans Postgres for
calls whose `last_step_started_at` is older than 7 minutes AND whose run
hasn't finished, and re-emits a `call/uploaded` event for each. Inngest's
own at-least-once delivery + the workflow's idempotent step boundaries
(Inngest memoizes by step_name+input_hash) mean a redispatch resumes
work rather than duplicating it.

Design choices:
  * 7-min threshold = ~1.75× the slowest legitimate step (analyze_checkpoints
    on a 60-min call ≈ 4 min p99). Anything past that is genuinely hung.
  * 1× max redispatch per call (`watchdog_redispatch_count < 1`) — prevents
    an infinite loop on permanently-broken inputs (corrupt audio, deleted
    storage key). Operators escalate manually after one redispatch fails.
  * Send the Inngest event BEFORE bumping the count: if `inngest.send`
    raises (Inngest dev server down, network blip), the count stays at 0
    so the next cron tick retries. If we bumped first and the send failed,
    the call would be marked "redispatched" forever and never recover.
  * `retries=0` on this function — re-running a missed minute is harmless,
    Inngest's own cron will fire again 60s later.
"""
from __future__ import annotations

from datetime import datetime
from app._clock import utcnow
from typing import Any

import inngest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.inngest_client import inngest_client
from app.logger import log as app_log
from app.workflows.events import CALL_UPLOADED


_STUCK_QUERY = text(
    """
    SELECT id, file_path, customer_name, deal_id, call_type, script_id
    FROM calls
    WHERE last_step_started_at < (NOW() - INTERVAL '7 minutes')
      AND completed_at IS NULL
      AND status NOT IN ('completed', 'failed')
      AND COALESCE(watchdog_redispatch_count, 0) < 1
    ORDER BY last_step_started_at ASC
    LIMIT 50
    """
)


# Wave 1: a stuck call that already burned its single redispatch is exhausted.
# We mark it failed and write the forensic failed_jobs row for the reviewer UI.
_EXHAUSTED_QUERY = text(
    """
    SELECT id, COALESCE(watchdog_redispatch_count, 0) AS attempts
    FROM calls
    WHERE last_step_started_at < (NOW() - INTERVAL '7 minutes')
      AND completed_at IS NULL
      AND status NOT IN ('completed', 'failed')
      AND COALESCE(watchdog_redispatch_count, 0) >= 1
    ORDER BY last_step_started_at ASC
    LIMIT 50
    """
)


@inngest_client.create_function(
    fn_id="redispatch-watchdog",
    trigger=inngest.TriggerCron(cron="* * * * *"),
    retries=0,
)
async def redispatch_watchdog(ctx: inngest.Context) -> dict:
    """Once-a-minute scan for stuck calls; re-emit CALL_UPLOADED for each."""
    from app.database import SessionLocal

    redispatched: list[str] = []
    db = SessionLocal()
    try:
        # Wave 1: when a stuck run has exceeded retry budget, mark the call
        # failed and record the forensic row in failed_jobs for the reviewer
        # UI. We do this BEFORE the redispatch scan so an exhausted call
        # never gets re-considered (status flips to 'failed', dropping it
        # out of both queries' WHERE clauses).
        exhausted_rows = db.execute(_EXHAUSTED_QUERY).fetchall()
        for row in exhausted_rows:
            _handle_exhausted_run(
                db, call_id=str(row.id), attempts=int(row.attempts)
            )
            db.commit()

        rows = db.execute(_STUCK_QUERY).fetchall()
        if not rows:
            return {"redispatched": [], "count": 0}

        for row in rows:
            call_id = str(row.id)
            payload: dict[str, Any] = {
                "call_id": call_id,
                "audio_path": row.file_path,
                "customer_name": row.customer_name,
                "deal_id": str(row.deal_id) if row.deal_id else None,
                "call_type": row.call_type,
                "script_id": row.script_id,
            }
            try:
                # IMPORTANT: send BEFORE incrementing the count. If send
                # fails the count stays so the next cron tick retries; if
                # we bumped first and the send failed the call would be
                # marked redispatched forever.
                await inngest_client.send(
                    inngest.Event(name=CALL_UPLOADED, data=payload)
                )
            except Exception as e:  # noqa: BLE001
                app_log.error(
                    f"WATCHDOG_REDISPATCH_SEND_FAIL call_id={call_id} err={e!r}"
                )
                continue

            try:
                db.execute(
                    text(
                        """
                        UPDATE calls
                        SET watchdog_redispatch_count = COALESCE(watchdog_redispatch_count, 0) + 1,
                            last_step_error = :msg
                        WHERE id = :id
                        """
                    ),
                    {
                        "id": call_id,
                        "msg": "watchdog: stuck > 7min, redispatched",
                    },
                )
                db.commit()
            except Exception as e:  # noqa: BLE001
                db.rollback()
                app_log.error(
                    f"WATCHDOG_REDISPATCH_BUMP_FAIL call_id={call_id} err={e!r}"
                )
                continue

            redispatched.append(call_id)
            app_log.info(
                f"WATCHDOG_REDISPATCH call_id={call_id} at={utcnow().isoformat()}Z"
            )
    finally:
        db.close()

    return {"redispatched": redispatched, "count": len(redispatched)}


def _handle_exhausted_run(db: Session, *, call_id: str, attempts: int) -> None:
    """Mark Call failed and write the forensic failed_jobs row.

    Reads `last_step_name` + `last_step_error` from the Call row (already
    populated by the per-step writer in process_call.py) so we never need
    the original Inngest payload.

    Concurrency note: `record_failed_job()` is idempotent on (call_id, attempts)
    via the unique index. If two concurrent watchdog ticks both call this
    function for the same exhausted run, the second insert will raise
    IntegrityError; we catch it and treat as success (write already happened).
    """
    from sqlalchemy.exc import IntegrityError
    from app.models import Call

    call = db.query(Call).filter_by(id=call_id).first()
    if call is None:
        return
    last_step = (getattr(call, "last_step_name", None) or "unknown")
    last_error = (getattr(call, "last_step_error", None) or "")
    try:
        record_failed_job(
            db, call_id=call_id, last_step=last_step,
            attempts=attempts, last_error=last_error,
        )
    except IntegrityError:
        db.rollback()  # second concurrent tick — already recorded by the first
        # Re-fetch call after rollback so subsequent status flip uses fresh state
        call = db.query(Call).filter_by(id=call_id).first()
        if call is None:
            return
    if call.status != "failed":
        call.status = "failed"


def record_failed_job(
    db: Session,
    *,
    call_id: str,
    last_step: str,
    attempts: int,
    last_error: str | None = None,
) -> None:
    """Insert one ``failed_jobs`` row. Idempotent on ``(call_id, attempts)``.

    Called by the watchdog when redispatch retries are exhausted, giving
    operators a queryable record of permanently-broken jobs to escalate.
    Caller is responsible for committing the session.

    The unique index ``ix_failed_jobs_call_attempt`` (mig ``6c863e1ce3b1``)
    is the canonical de-dup at the DB layer; the in-Python check below
    short-circuits the round-trip in the common case (next watchdog tick
    re-inspects the same exhausted call) without relying on integrity-error
    handling.
    """
    from app.models import FailedJob

    existing = (
        db.query(FailedJob)
        .filter_by(call_id=call_id, attempts=attempts)
        .first()
    )
    if existing is not None:
        return
    db.add(
        FailedJob(
            call_id=call_id,
            last_step=last_step,
            attempts=attempts,
            last_error=(last_error or "")[:4000],
        )
    )
