"""Backfill word_data + assemblyai_transcript for calls missing them.

Runs AssemblyAI on every call that has a file_path but no word_data.
Sequential (one at a time) to respect the free-tier rate limit. Safe to
re-run — already-backfilled calls are skipped.

Usage:
    cd backend && ./venv/bin/python3 backfill_word_data.py
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

# Make `app.*` imports work from this file
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.assemblyai_transcription import transcribe_audio_assemblyai
from app.database import SessionLocal
from app.models import Call


async def process_one(call: Call, idx: int, total: int) -> str:
    if not call.file_path or not os.path.exists(call.file_path):
        return "skipped: missing file"
    if call.word_data and len(call.word_data) > 100:
        return "skipped: already has word_data"

    t0 = time.time()
    try:
        result = await transcribe_audio_assemblyai(call.file_path)
    except Exception as e:
        return f"error: {type(e).__name__}: {e}"

    elapsed = time.time() - t0
    words = result.get("words", [])
    transcript = result.get("transcript", "")

    db = SessionLocal()
    try:
        call_db = db.query(Call).filter_by(id=call.id).first()
        if call_db is None:
            return "error: call vanished"
        call_db.word_data = json.dumps(words)
        call_db.assemblyai_transcript = transcript
        db.commit()
    finally:
        db.close()

    return f"ok: {len(words)} words in {elapsed:.1f}s"


async def main():
    db = SessionLocal()
    try:
        calls = db.query(Call).all()
    finally:
        db.close()

    needing = [c for c in calls if not c.word_data or len(c.word_data or "") < 100]
    print(f"Total calls: {len(calls)}  |  Needing backfill: {len(needing)}")
    if not needing:
        print("Nothing to do.")
        return

    for i, call in enumerate(needing, 1):
        name = (call.filename or call.id)[:60]
        print(f"[{i}/{len(needing)}] {call.id[:8]}  {name}", end="  ... ", flush=True)
        msg = await process_one(call, i, len(needing))
        print(msg)


if __name__ == "__main__":
    asyncio.run(main())
