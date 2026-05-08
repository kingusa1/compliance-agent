#!/usr/bin/env python3
"""Redispatch the call/uploaded Inngest event for one or more orphan calls.

Usage:
    ./venv/bin/python redispatch_call.py <call_id> [<call_id> ...]
    ./venv/bin/python redispatch_call.py --all-stuck   # every status=processing row
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta

import inngest

from app.database import SessionLocal
from app.inngest_client import inngest_client
from app.models import Call
from app.workflows.events import CALL_UPLOADED


async def _send(call: Call) -> None:
    await inngest_client.send(
        inngest.Event(
            name=CALL_UPLOADED,
            data={
                "call_id": str(call.id),
                "audio_path": call.file_path,
                "customer_name": call.customer_name,
                "deal_id": str(call.deal_id) if call.deal_id else None,
                "call_type": call.call_type,
                "script_id": call.script_id,
            },
        )
    )
    print(f"redispatched call_id={call.id} filename={call.filename!r}")


async def main(args: list[str]) -> None:
    db = SessionLocal()
    try:
        if args == ["--all-stuck"]:
            cutoff = datetime.utcnow() - timedelta(seconds=30)
            calls = (
                db.query(Call)
                .filter(Call.status == "processing", Call.created_at < cutoff)
                .all()
            )
            print(f"found {len(calls)} stuck calls")
        else:
            calls = db.query(Call).filter(Call.id.in_(args)).all()
            missing = set(args) - {c.id for c in calls}
            if missing:
                print(f"warning: not found: {missing}", file=sys.stderr)
        for c in calls:
            await _send(c)
    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    asyncio.run(main(sys.argv[1:]))
