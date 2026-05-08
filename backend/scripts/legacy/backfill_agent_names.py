"""Backfill agent_name + customer_name for calls where they are 'Unknown'.

Reads each Unknown call's transcript, runs detect_names (one cheap LLM call),
and updates the DB. Idempotent — safe to re-run.

Usage:
    cd backend && ./venv/bin/python3 backfill_agent_names.py
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.analysis import detect_names
from app.database import SessionLocal
from app.models import Call


async def main():
    db = SessionLocal()
    try:
        calls = db.query(Call).all()
    finally:
        db.close()

    needing = [
        c for c in calls
        if (not c.agent_name or c.agent_name == "Unknown") and c.transcript
    ]
    print(f"Total calls: {len(calls)}  |  Needing agent-name backfill: {len(needing)}")
    if not needing:
        print("Nothing to do.")
        return

    for i, call in enumerate(needing, 1):
        name = (call.filename or call.id)[:60]
        print(f"[{i}/{len(needing)}] {call.id[:8]}  {name}", end="  ... ", flush=True)
        t0 = time.time()
        try:
            agent, customer = await detect_names(call.transcript)
        except Exception as e:
            print(f"error: {e}")
            continue

        db = SessionLocal()
        try:
            call_db = db.query(Call).filter_by(id=call.id).first()
            if call_db is None:
                print("vanished")
                continue
            if agent and agent != "Unknown":
                call_db.agent_name = agent
            if customer and customer != "Unknown":
                call_db.customer_name = customer
            db.commit()
        finally:
            db.close()

        print(f"agent=\"{agent}\" customer=\"{customer}\" ({time.time()-t0:.1f}s)")


if __name__ == "__main__":
    asyncio.run(main())
