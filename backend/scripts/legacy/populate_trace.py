"""
Re-analyze ONE existing call through the agent path so the UI's
Agent Trace view has something to render.

Usage: python3 backend/populate_trace.py [call_id_prefix]
Default: picks the first completed non-pending call.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from app.config import settings
settings.use_agent_analyzer = True  # force agent path for this run

from app.database import SessionLocal
from app.models import Call, Script, AgentTrace
from app.checkpoint_analyzer import analyze_all_checkpoints


async def main():
    prefix = sys.argv[1] if len(sys.argv) > 1 else None
    db = SessionLocal()

    q = db.query(Call).filter(Call.status == "completed", Call.transcript.isnot(None))
    if prefix:
        q = q.filter(Call.id.like(f"{prefix}%"))
    call = q.order_by(Call.created_at.desc()).first()
    if not call:
        print("No completed call found.")
        sys.exit(1)

    script = db.query(Script).filter_by(id=call.script_id).first()
    if not script:
        print(f"No script for call {call.id}")
        sys.exit(1)

    checkpoints = json.loads(script.checkpoints)
    word_data = json.loads(call.word_data) if call.word_data else []

    print(f"Call      : {call.id}")
    print(f"Filename  : {call.filename}")
    print(f"Supplier  : {call.detected_supplier}")
    print(f"Checkpoints: {len(checkpoints)}")
    print(f"Running agent analyzer (settings.use_agent_analyzer = {settings.use_agent_analyzer})…\n")

    before = db.query(AgentTrace).filter_by(call_id=call.id).count()
    print(f"Traces in DB for this call BEFORE: {before}")

    result = await analyze_all_checkpoints(
        call.transcript,
        checkpoints,
        script.mode,
        supplier=script.supplier_name,
        word_data=word_data,
        agent_speaker_label="A",
        customer_speaker_label="B",
        db=db,
        call_id=call.id,
    )

    after = db.query(AgentTrace).filter_by(call_id=call.id).count()
    print(f"Traces in DB for this call AFTER : {after}  (+{after - before})\n")

    # Show per-checkpoint trace counts
    from sqlalchemy import func
    rows = (
        db.query(AgentTrace.checkpoint_id, func.count(AgentTrace.id).label("n"))
        .filter_by(call_id=call.id)
        .group_by(AgentTrace.checkpoint_id)
        .all()
    )
    print("Trace rows per checkpoint_id:")
    for r in rows:
        print(f"  {r.checkpoint_id}: {r.n} turns")

    print(f"\nOpen in browser: http://localhost:3004/calls/{call.id}")


if __name__ == "__main__":
    asyncio.run(main())
