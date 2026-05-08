"""Replay path — re-derive a call's verdict from its stored transcript.

Cost model: zero re-transcription, zero new audio I/O. Pipeline steps 4
(analyze_checkpoints) -> 5 (score) -> 6 (finalize) re-run via the Inngest
``call/reanalyze`` event. Existing CallCheckpoint idempotency replaces
prior rows (the workflow function uses delete-and-insert by call_id +
checkpoint_index, so reruns don't pile up).
"""
from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.models import Call
from app.workflows.events import CALL_REANALYZE
from app.workflows.observability import emit_event_async


async def reanalyze(call_id: str, db: Session, actor_id: str | None = None) -> dict:
    call = db.query(Call).filter(Call.id == call_id).first()
    if call is None:
        raise HTTPException(status_code=404, detail=f"Call {call_id} not found")
    if not call.transcript or not call.word_data or not call.script_id:
        raise HTTPException(
            status_code=422,
            detail="Call lacks transcript / word_data / script_id — cannot reanalyze.",
        )

    run_id = str(uuid.uuid4())
    payload = {"call_id": call_id, "run_id": run_id, "actor": actor_id}
    await emit_event_async(CALL_REANALYZE, payload)
    record_audit(
        db,
        action="reanalyze",
        entity_type="call",
        entity_id=call_id,
        payload=payload,
        actor_id=actor_id,
    )
    db.commit()
    return payload
