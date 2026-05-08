"""L6 RAG ingestion workflows.

Two Inngest functions:

  • `rag_ingest_call`   — triggered by `call/finalized` (emitted by the
                          finalize step in process_call.py once main lands
                          that change). Chunks transcript + embeds + writes
                          transcript_chunks rows.

  • `rag_ingest_script` — triggered by `script/changed` (emitted by script
                          CRUD endpoints). Chunks the script's checkpoints
                          + embeds + writes script_chunks rows.

Both run as separate post-finalize functions so they never block the main
pipeline or interact with the L1 stuck-watchdog (watchdog filters on
completed_at IS NOT NULL, which is set by finalize).
"""
from __future__ import annotations

import asyncio

import inngest

from app.database import SessionLocal
from app.inngest_client import inngest_client
from app.logger import log as app_log
from app.rag.ingest import ingest_call, ingest_script

CALL_FINALIZED = "call/finalized"
SCRIPT_CHANGED = "script/changed"


@inngest_client.create_function(
    fn_id="rag-ingest-call",
    trigger=inngest.TriggerEvent(event=CALL_FINALIZED),
    retries=3,
)
async def rag_ingest_call_fn(ctx: inngest.Context) -> dict:
    data = ctx.event.data or {}
    call_id = data.get("call_id")
    if not call_id:
        raise RuntimeError(f"rag-ingest-call missing call_id: {data!r}")

    app_log.info(f"RAG_INGEST_CALL_START call_id={call_id}")

    def _run():
        db = SessionLocal()
        try:
            return ingest_call(call_id, db)
        finally:
            db.close()

    result = await ctx.step.run("ingest_call", lambda: asyncio.to_thread(_run))
    return {"call_id": call_id, **(result or {})}


@inngest_client.create_function(
    fn_id="rag-ingest-script",
    trigger=inngest.TriggerEvent(event=SCRIPT_CHANGED),
    retries=3,
)
async def rag_ingest_script_fn(ctx: inngest.Context) -> dict:
    data = ctx.event.data or {}
    script_id = data.get("script_id")
    if not script_id:
        raise RuntimeError(f"rag-ingest-script missing script_id: {data!r}")

    app_log.info(f"RAG_INGEST_SCRIPT_START script_id={script_id}")

    def _run():
        db = SessionLocal()
        try:
            return ingest_script(script_id, db)
        finally:
            db.close()

    result = await ctx.step.run("ingest_script", lambda: asyncio.to_thread(_run))
    return {"script_id": script_id, **(result or {})}
