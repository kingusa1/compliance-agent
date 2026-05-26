"""Groq Whisper Large v3 transcription.

Fast (~4.5s), free tier, 86.7% cross-model agreement in our benchmark.
Returns plain text transcript only (no per-word data).
"""
from pathlib import Path

import anyio
import httpx

from app.config import settings
from app.logger import log


def _read_file_bytes(file_path: str) -> bytes:
    """Synchronous file read isolated for `anyio.to_thread.run_sync` off-loading
    (2026-05-27 LAG FIX — same pattern as other transcribers)."""
    with open(file_path, "rb") as f:
        return f.read()


async def transcribe_audio_groq(file_path: str) -> str | None:
    """Transcribe with Whisper Large v3 via Groq. Returns None on failure."""
    api_key = settings.groq_api_key if hasattr(settings, "groq_api_key") else ""
    if not api_key:
        import os
        api_key = os.getenv("GROQ_API_KEY", "")
    if not api_key:
        return None

    try:
        # 2026-05-27 LAG FIX: pre-read bytes off-loop instead of letting httpx
        # multipart construction read the file handle synchronously. Use
        # `anyio.to_thread.run_sync` so the read consumes the 200-token AnyIO
        # limiter set in `main.py` (asyncio.to_thread bypasses it).
        audio_bytes = await anyio.to_thread.run_sync(_read_file_bytes, file_path)
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": (Path(file_path).name, audio_bytes, "audio/mpeg")},
                data={"model": "whisper-large-v3", "response_format": "json"},
            )
        resp.raise_for_status()
        text = resp.json().get("text", "")
        log.info(f"\U0001f399️ GROQ Whisper LV3 done → {len(text.split())} words")
        return text
    except OSError as io_e:
        # Distinct from API failures so on-call can tell "file missing"
        # from "Groq returned 5xx" at a glance (2026-05-27 reviewer LOW).
        log.error(f"⚠️ GROQ Whisper file read failed path={file_path}: {io_e}")
        return None
    except Exception as e:
        log.warning(f"⚠️ GROQ Whisper failed: {e}")
        return None
