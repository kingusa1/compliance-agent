"""Groq Whisper Large v3 transcription.

Fast (~4.5s), free tier, 86.7% cross-model agreement in our benchmark.
Returns plain text transcript only (no per-word data).
"""
from pathlib import Path

import httpx

from app.config import settings
from app.logger import log


async def transcribe_audio_groq(file_path: str) -> str | None:
    """Transcribe with Whisper Large v3 via Groq. Returns None on failure."""
    api_key = settings.groq_api_key if hasattr(settings, "groq_api_key") else ""
    if not api_key:
        import os
        api_key = os.getenv("GROQ_API_KEY", "")
    if not api_key:
        return None

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            with open(file_path, "rb") as f:
                resp = await client.post(
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    files={"file": (Path(file_path).name, f, "audio/mpeg")},
                    data={"model": "whisper-large-v3", "response_format": "json"},
                )
        resp.raise_for_status()
        text = resp.json().get("text", "")
        log.info(f"\U0001f399\ufe0f GROQ Whisper LV3 done \u2192 {len(text.split())} words")
        return text
    except Exception as e:
        log.warning(f"\u26a0\ufe0f GROQ Whisper failed: {e}")
        return None
