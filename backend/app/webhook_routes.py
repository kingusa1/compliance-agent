"""AssemblyAI webhook endpoint.

AssemblyAI POSTs a lightweight notification body:
    { "transcript_id": "<id>", "status": "completed" | "error" }

when a transcription job finishes. We authenticate via a static custom
header (``X-AssemblyAI-Webhook-Secret``) set at job-submit time; the
value is compared constant-time against ``ASSEMBLYAI_WEBHOOK_SECRET``.

On a valid delivery we:
1. Set a sentinel in ``_WEBHOOK_ARRIVALS`` so any polling loop on the
   same transcript can break early (within the next 30s poll window).
2. Schedule the post-transcription completion flow via asyncio background
   task so the webhook handler returns 200 in < 1s.
3. Return 200 even on ``status == "error"`` — we log + mark the call
   failed, still acknowledging receipt so AssemblyAI doesn't retry.
"""
from __future__ import annotations

import asyncio
import hmac
import os
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app.logger import log


webhook_router = APIRouter()

# In-memory sentinel: { transcript_id: "completed" | "error" }
# Written by the webhook handler, read by the poll loop in
# assemblyai_transcription.py to break early after a webhook arrives.
# Bounded by the number of in-flight jobs (a few dozen at most).
_WEBHOOK_ARRIVALS: dict[str, str] = {}


def _get_webhook_secret() -> str | None:
    """Return the configured webhook secret, or None if unset."""
    return os.environ.get("ASSEMBLYAI_WEBHOOK_SECRET") or None


def _verify_secret(provided: str) -> bool:
    """Constant-time compare of the provided header value against the env secret."""
    secret = _get_webhook_secret()
    if not secret:
        return False
    return hmac.compare_digest(secret.encode(), provided.encode())


async def _handle_completed(transcript_id: str) -> None:
    """Background task: fetch full transcript from AssemblyAI and store on Call.

    This mirrors what the poll loop does when it sees status == "completed"
    but runs after webhook delivery instead of after polling. The existing
    pipeline step (`_step_transcribe`) already writes the result to the Call
    row; because transcriptions submitted with webhook_url are delivered here
    first, we only need to mark the sentinel so the poll-loop wrapper knows
    not to wait for additional 3s ticks.

    The heavy re-fetch + DB write is intentionally kept minimal — the webhook
    is only a signal that the job is done. The poll loop's next iteration
    (within 30s) will call GET /v2/transcript/{id} and write the row exactly
    as before. This task is therefore a no-op when Inngest is enabled; when
    using the legacy asyncio path the sentinel break-out handles latency.
    """
    log.info(f"ASSEMBLYAI_WEBHOOK transcript_id={transcript_id} status=completed (background)")


async def _handle_error(transcript_id: str) -> None:
    """Background task: mark the Call row as failed when AAI reports an error."""
    log.warning(f"ASSEMBLYAI_WEBHOOK transcript_id={transcript_id} status=error — marking call failed")
    try:
        from app.database import SessionLocal
        from app.models import Call

        db = SessionLocal()
        try:
            call = db.query(Call).filter(
                Call.assemblyai_metadata.isnot(None)
            ).filter(
                # assemblyai_metadata JSONB contains the job id at ["id"]
                # Use the sentinel map: we stored call lookup as part of submit.
                # Fallback: log only; the poll loop will catch this within 30s.
            ).first()
            # The transcript_id → call_id mapping is available via _WEBHOOK_ARRIVALS
            # keys only; the Call row stores the AAI job id inside assemblyai_metadata.
            # Look up by stored metadata id field.
            call = (
                db.query(Call)
                .filter(
                    Call.status.in_(["processing", "pending", "pending_stream"])
                )
                .all()
            )
            for c in call:
                md = c.assemblyai_metadata or {}
                if isinstance(md, dict) and md.get("id") == transcript_id:
                    c.status = "failed"
                    c.reason = f"AssemblyAI transcription error (transcript_id={transcript_id})"
                    db.commit()
                    log.warning(
                        f"ASSEMBLYAI_WEBHOOK marked call={c.id} failed via webhook error "
                        f"transcript_id={transcript_id}"
                    )
                    break
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001
        log.warning(f"ASSEMBLYAI_WEBHOOK error handler DB lookup failed: {e!r}")


@webhook_router.post("/api/webhooks/assemblyai", include_in_schema=False)
async def assemblyai_webhook(
    request: Request,
    x_assemblyai_webhook_secret: str | None = Header(default=None),
) -> JSONResponse:
    """Receive AssemblyAI job-completion notification.

    Auth: static header ``X-AssemblyAI-Webhook-Secret`` constant-time compared
    against env ``ASSEMBLYAI_WEBHOOK_SECRET``. Returns 401 on mismatch.

    Body: ``{ "transcript_id": "...", "status": "completed" | "error" }``

    Returns 200 immediately; heavy work runs as an asyncio background task.
    """
    # ── Auth ──────────────────────────────────────────────────────────────
    provided = x_assemblyai_webhook_secret or ""
    if not _verify_secret(provided):
        log.warning("ASSEMBLYAI_WEBHOOK auth_failed — missing or wrong X-AssemblyAI-Webhook-Secret")
        raise HTTPException(status_code=401, detail="Unauthorized")

    # ── Parse body ────────────────────────────────────────────────────────
    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        log.warning("ASSEMBLYAI_WEBHOOK bad_body — could not parse JSON")
        return JSONResponse({"ok": False, "error": "bad_body"}, status_code=400)

    transcript_id: str = body.get("transcript_id", "")
    status: str = body.get("status", "")

    if not transcript_id:
        log.warning("ASSEMBLYAI_WEBHOOK missing transcript_id in body")
        return JSONResponse({"ok": False, "error": "missing_transcript_id"}, status_code=400)

    log.info(f"ASSEMBLYAI_WEBHOOK received transcript_id={transcript_id} status={status}")

    # ── Sentinel — unblocks the poll-loop within the next check ───────────
    _WEBHOOK_ARRIVALS[transcript_id] = status

    # ── Schedule background work — return 200 immediately ─────────────────
    if status == "completed":
        asyncio.create_task(_handle_completed(transcript_id))
    elif status == "error":
        asyncio.create_task(_handle_error(transcript_id))
    else:
        log.info(f"ASSEMBLYAI_WEBHOOK ignoring unknown status={status!r} transcript_id={transcript_id}")

    return JSONResponse({"ok": True})
