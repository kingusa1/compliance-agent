"""Cohere audio transcription.

87.4% cross-model agreement in our 6-model benchmark (second overall).
Returns plain text. Free tier available.
"""
import os
from pathlib import Path

import httpx

from app.logger import log


async def transcribe_audio_cohere(file_path: str) -> str | None:
    """Transcribe with Cohere. Returns None on failure."""
    api_key = os.getenv("COHERE_API_KEY", "")
    if not api_key:
        return None

    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            with open(file_path, "rb") as f:
                resp = await client.post(
                    "https://api.cohere.com/v2/audio/transcriptions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    files={"file": (Path(file_path).name, f, "audio/mpeg")},
                    data={"model": "cohere-transcribe-03-2026"},
                )
        resp.raise_for_status()
        data = resp.json()
        text = data.get("text") or data.get("transcript") or ""
        log.info(f"\U0001f399\ufe0f COHERE done \u2192 {len(text.split())} words")
        return text or None
    except Exception as e:
        log.warning(f"\u26a0\ufe0f COHERE failed: {e}")
        return None
