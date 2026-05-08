"""Sentence-window chunking for L6 RAG ingestion.

No spaCy — uses a regex on `[.!?] ` boundaries. 3-sentence sliding window
with 50% overlap (advance by 1 sentence per step). Chunks expose speaker
and start_s/end_s when word-level data is provided.

`chunk_script` produces one chunk per script checkpoint, concatenating
name + expected_phrases (or `key_phrases` for back-compat with existing
parser output) + description (or `required`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


@dataclass
class Chunk:
    chunk_idx: int
    text: str
    speaker: str | None
    start_s: float | None
    end_s: float | None


def _word_offset(text: str, sub: str, start_at: int) -> int:
    """Find `sub` in `text` from `start_at`. Returns index or -1."""
    return text.find(sub, start_at)


def chunk_transcript(transcript: str, word_data: list[dict] | None) -> list[Chunk]:
    """Split transcript into 3-sentence sliding windows advancing by 1 sentence.

    Speaker is taken from the first word of the window. start_s/end_s come
    from the first/last word that overlaps the chunk's character range.
    Falls back to None for speaker/timestamps when word_data is missing.
    """
    if not transcript or not transcript.strip():
        return []

    sentences = [s for s in _SENT_SPLIT.split(transcript.strip()) if s.strip()]
    if not sentences:
        return []

    # Build (sentence_text, char_start, char_end) so we can map to words.
    spans: list[tuple[str, int, int]] = []
    cursor = 0
    for s in sentences:
        idx = transcript.find(s, cursor)
        if idx < 0:
            idx = cursor
        spans.append((s, idx, idx + len(s)))
        cursor = idx + len(s)

    chunks: list[Chunk] = []
    window = 3
    chunk_idx = 0
    for i in range(0, max(1, len(sentences) - window + 1)):
        win = spans[i : i + window]
        text = " ".join(s for s, _, _ in win).strip()
        char_start = win[0][1]
        char_end = win[-1][2]

        speaker: str | None = None
        start_s: float | None = None
        end_s: float | None = None

        if word_data:
            # Walk the transcript to locate words inside [char_start, char_end].
            # We don't have per-word char offsets, so we approximate by token
            # order: each word in word_data corresponds to a token in the
            # transcript in order. Use a running text rebuild to find matches.
            running = 0
            picked: list[dict] = []
            for w in word_data:
                token = w.get("punctuated_word") or w.get("word") or ""
                if not token:
                    continue
                pos = transcript.find(token, running)
                if pos < 0:
                    running += len(token) + 1
                    continue
                running = pos + len(token)
                if pos >= char_start and pos < char_end:
                    picked.append(w)
                if pos >= char_end:
                    break
            if picked:
                speaker = picked[0].get("speaker")
                start_s = picked[0].get("start")
                end_s = picked[-1].get("end")

        chunks.append(
            Chunk(
                chunk_idx=chunk_idx,
                text=text,
                speaker=speaker,
                start_s=start_s,
                end_s=end_s,
            )
        )
        chunk_idx += 1

        if len(sentences) <= window:
            break  # only one chunk possible

    return chunks


def chunk_script(script: Any) -> list[Chunk]:
    """Produce one Chunk per script checkpoint.

    Accepts either a dict-like with `checkpoints` (JSON-decoded list) or a
    list of checkpoint dicts directly. Each chunk's text combines the
    checkpoint name + phrases + description so embeddings capture both
    intent and expected language.
    """
    checkpoints: list[dict]
    if isinstance(script, list):
        checkpoints = script
    elif isinstance(script, dict):
        checkpoints = script.get("checkpoints") or []
    else:
        checkpoints = getattr(script, "checkpoints", None) or []
        if isinstance(checkpoints, str):
            import json as _json

            try:
                checkpoints = _json.loads(checkpoints)
            except Exception:
                checkpoints = []

    chunks: list[Chunk] = []
    for idx, cp in enumerate(checkpoints):
        if not isinstance(cp, dict):
            continue
        name = (cp.get("name") or "").strip()
        # Architect spec: expected_phrases / description.
        # Existing parser emits: key_phrases / required.
        phrases = cp.get("expected_phrases") or cp.get("key_phrases") or []
        if not isinstance(phrases, list):
            phrases = []
        description = (cp.get("description") or cp.get("required") or "").strip()
        text = f"{name}. {' '.join(phrases)}. {description}".strip()
        chunks.append(
            Chunk(
                chunk_idx=idx,
                text=text,
                speaker=None,
                start_s=None,
                end_s=None,
            )
        )
    return chunks
