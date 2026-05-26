"""Cohere audio transcription.

87.4% cross-model agreement in our 6-model benchmark (second overall).
Returns plain text. Free tier available.
"""
import os
from pathlib import Path

import anyio
import httpx

from app.logger import log


def _read_file_bytes(file_path: str) -> bytes:
    """Synchronous file read isolated for `anyio.to_thread.run_sync` off-loading
    (2026-05-27 LAG FIX — same pattern as transcription._read_file_bytes)."""
    with open(file_path, "rb") as f:
        return f.read()


async def transcribe_audio_cohere(file_path: str) -> str | None:
    """Transcribe with Cohere. Returns None on failure."""
    api_key = os.getenv("COHERE_API_KEY", "")
    if not api_key:
        return None

    try:
        # 2026-05-27 LAG FIX: pre-read bytes off-loop instead of passing the
        # file handle to httpx.files= which reads it synchronously during
        # multipart body construction. The original code starved the event
        # loop on 1-5MB MP3 uploads under bulk concurrency. Use
        # `anyio.to_thread.run_sync` so the read consumes the 200-token
        # AnyIO limiter set in `main.py` (asyncio.to_thread bypasses it).
        audio_bytes = await anyio.to_thread.run_sync(_read_file_bytes, file_path)
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(
                "https://api.cohere.com/v2/audio/transcriptions",
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": (Path(file_path).name, audio_bytes, "audio/mpeg")},
                data={"model": "cohere-transcribe-03-2026"},
            )
        resp.raise_for_status()
        data = resp.json()
        text = data.get("text") or data.get("transcript") or ""
        log.info(f"\U0001f399️ COHERE done → {len(text.split())} words")
        return text or None
    except OSError as io_e:
        # Distinct from API failures so on-call can tell "file missing"
        # from "Cohere returned 5xx" at a glance (2026-05-27 reviewer LOW).
        log.error(f"⚠️ COHERE file read failed path={file_path}: {io_e}")
        return None
    except Exception as e:
        log.warning(f"⚠️ COHERE failed: {e}")
        return None
