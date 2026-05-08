"""Embedding-similarity pre-filter for compliance checkpoints.

Goal: avoid spending LLM tokens analysing checkpoints that obviously
aren't covered in the transcript (e.g. a "vulnerable customer" checkpoint
on a sales-only conversation). For each checkpoint, embed its
name + description; for the transcript, embed the whole text as one chunk
(or split into N chunks for long calls). Cosine-sim each (chunk, checkpoint)
pair, keep checkpoints whose top similarity score >= threshold.

Failure mode discipline: if the embedding API is unavailable or returns
malformed output, return ALL checkpoints unfiltered. NEVER silently
drop checkpoints — that would produce false-pass compliance verdicts.
The pre-filter is a cost optimisation; correctness wins over cost.
"""
from __future__ import annotations

import math
from typing import Iterable

from app.logger import log
from app.rag.embed import embed_batch


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _chunk_transcript(text: str, max_chars: int = 1500) -> list[str]:
    """Split transcript into ~1.5KB chunks at sentence boundaries.

    1500 chars ≈ 250 words ≈ 90s of typical call audio, which fits well
    inside text-embedding-3-small's 8192-token context with headroom.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    parts = text.replace("\n", " ").split(". ")
    chunks: list[str] = []
    cur = ""
    for p in parts:
        candidate = (cur + ". " + p).strip(". ") if cur else p
        if len(candidate) > max_chars and cur:
            chunks.append(cur)
            cur = p
        else:
            cur = candidate
    if cur:
        chunks.append(cur)
    return chunks


def _checkpoint_text(cp: dict) -> str:
    name = (cp.get("name") or "").strip()
    desc = (cp.get("description") or "").strip()
    return f"{name}. {desc}" if desc else name


def select_relevant_checkpoints(
    transcript: str,
    checkpoints: list[dict],
    threshold: float = 0.35,
) -> list[dict]:
    """Return checkpoints whose top chunk-similarity >= threshold.

    Empty transcript or empty checkpoints → []. Embedding failure →
    return all checkpoints (graceful degrade — correctness over cost).
    """
    if not checkpoints:
        return []
    if not transcript or not transcript.strip():
        return []

    chunks = _chunk_transcript(transcript)
    if not chunks:
        return []

    try:
        chunk_vecs = embed_batch(chunks)
        cp_texts = [_checkpoint_text(cp) for cp in checkpoints]
        cp_vecs = embed_batch(cp_texts)
    except Exception as e:  # noqa: BLE001 — pre-filter must not break business path
        log.warning(f"PREFILTER_EMBED_FAILED err={type(e).__name__}: {e} — returning all checkpoints")
        return list(checkpoints)

    if len(chunk_vecs) != len(chunks) or len(cp_vecs) != len(checkpoints):
        log.warning("PREFILTER_EMBED_SHAPE_MISMATCH — returning all checkpoints")
        return list(checkpoints)

    kept: list[dict] = []
    dropped = 0
    for cp, cp_vec in zip(checkpoints, cp_vecs):
        top = max((_cosine(cv, cp_vec) for cv in chunk_vecs), default=0.0)
        if top >= threshold:
            kept.append(cp)
        else:
            dropped += 1
    log.info(
        f"PREFILTER kept={len(kept)} dropped={dropped} threshold={threshold:.2f} chunks={len(chunks)}"
    )
    return kept
