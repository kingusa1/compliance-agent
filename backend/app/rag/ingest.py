"""L6 RAG ingestion: chunk + embed + persist.

`ingest_call` runs after a call finalizes (triggered by `call/finalized`
Inngest event). `ingest_script` runs on script CRUD (`script/changed`).

Both are idempotent: existing rows for the same call_id / script_version_id
are deleted before inserting the new batch. If OPENAI_API_KEY is unset,
embeddings are skipped silently and rows are written with embedding=NULL
(graceful_degrade per L6 design — search.py reports embeddings_available=False).

The TranscriptChunk / ScriptChunk ORM classes are added by main on
app/models.py; this module imports them defensively so it can co-exist
with branches that haven't merged the migration yet.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models import Call, Script, ScriptVersion
from app.rag.chunker import Chunk, chunk_script, chunk_transcript
from app.rag.embed import embed_batch

logger = logging.getLogger(__name__)


def _import_chunk_models():
    """Defensive: TranscriptChunk / ScriptChunk are added by main on models.py.

    Returns (TranscriptChunk, ScriptChunk) or (None, None) if unavailable.
    """
    try:
        from app.models import TranscriptChunk, ScriptChunk  # type: ignore

        return TranscriptChunk, ScriptChunk
    except Exception:
        return None, None


def _try_embed(texts: list[str]) -> tuple[list[list[float]] | None, bool]:
    """Embed `texts` if possible. Returns (vectors, embedded_flag).

    `vectors` is None when OPENAI_API_KEY is unset; the caller writes rows
    with embedding=NULL so a future backfill can fill them in.
    """
    if not texts:
        return [], False
    try:
        return embed_batch(texts), True
    except EnvironmentError as e:
        logger.warning("RAG embed skipped: %s", e)
        return None, False
    except Exception as e:  # noqa: BLE001
        logger.warning("RAG embed failed: %s — writing rows with NULL embedding", e)
        return None, False


def ingest_call(call_id: str, db: Session) -> dict[str, Any]:
    """Chunk + embed + persist transcript for one call. Idempotent.

    Returns {"chunks": int, "embedded": bool} for logging.
    """
    TranscriptChunk, _ = _import_chunk_models()
    if TranscriptChunk is None:
        logger.warning("RAG_INGEST_CALL skipped — TranscriptChunk model unavailable")
        return {"chunks": 0, "embedded": False, "skipped": True}

    call = db.query(Call).filter(Call.id == call_id).one_or_none()
    if call is None:
        logger.warning("RAG_INGEST_CALL call_id=%s not found", call_id)
        return {"chunks": 0, "embedded": False}

    transcript = (
        call.assemblyai_transcript
        or call.gemini_transcript
        or call.transcript
        or ""
    )
    word_data: list[dict] = []
    if call.word_data:
        try:
            word_data = json.loads(call.word_data) if isinstance(call.word_data, str) else call.word_data
        except Exception:
            word_data = []

    chunks: list[Chunk] = chunk_transcript(transcript, word_data)
    vectors, embedded = _try_embed([c.text for c in chunks])

    # Idempotent: clear prior rows for this call.
    db.query(TranscriptChunk).filter(TranscriptChunk.call_id == call_id).delete()

    rows = []
    for i, ch in enumerate(chunks):
        emb = vectors[i] if vectors is not None and i < len(vectors) else None
        rows.append(
            TranscriptChunk(
                call_id=call_id,
                chunk_idx=ch.chunk_idx,
                text=ch.text,
                speaker=ch.speaker,
                start_s=ch.start_s,
                end_s=ch.end_s,
                embedding=emb,
            )
        )
    if rows:
        db.add_all(rows)
    db.commit()

    logger.info(
        "RAG_INGEST_CALL call_id=%s chunks=%d embedded=%s",
        call_id, len(rows), "true" if embedded else "false",
    )
    return {"chunks": len(rows), "embedded": embedded}


def ingest_script(script_id: str, db: Session) -> dict[str, Any]:
    """Chunk + embed + persist script checkpoints. Idempotent on script_version_id.

    Uses the latest ScriptVersion when one exists; falls back to Script.checkpoints
    JSON otherwise.
    """
    _, ScriptChunk = _import_chunk_models()
    if ScriptChunk is None:
        logger.warning("RAG_INGEST_SCRIPT skipped — ScriptChunk model unavailable")
        return {"chunks": 0, "embedded": False, "skipped": True}

    script = db.query(Script).filter(Script.id == script_id).one_or_none()
    if script is None:
        logger.warning("RAG_INGEST_SCRIPT script_id=%s not found", script_id)
        return {"chunks": 0, "embedded": False}

    latest_version = (
        db.query(ScriptVersion)
        .filter(ScriptVersion.script_id == script_id)
        .order_by(ScriptVersion.version_number.desc())
        .first()
    )
    if latest_version is not None:
        try:
            checkpoints = json.loads(latest_version.checkpoints_snapshot or "[]")
        except Exception:
            checkpoints = []
        version_id = latest_version.id
    else:
        try:
            checkpoints = json.loads(script.checkpoints or "[]")
        except Exception:
            checkpoints = []
        version_id = None

    chunks = chunk_script(checkpoints)
    vectors, embedded = _try_embed([c.text for c in chunks])

    # Idempotent: clear prior rows. Filter by script_version_id when available,
    # else by script_id alone (covers branches without script_version_id col).
    q = db.query(ScriptChunk).filter(ScriptChunk.script_id == script_id)
    if version_id is not None and hasattr(ScriptChunk, "script_version_id"):
        q = db.query(ScriptChunk).filter(ScriptChunk.script_version_id == version_id)
    q.delete()

    rows = []
    for i, ch in enumerate(chunks):
        emb = vectors[i] if vectors is not None and i < len(vectors) else None
        kwargs: dict[str, Any] = {
            "script_id": script_id,
            "checkpoint_idx": ch.chunk_idx,
            "text": ch.text,
            "embedding": emb,
        }
        if version_id is not None and hasattr(ScriptChunk, "script_version_id"):
            kwargs["script_version_id"] = version_id
        rows.append(ScriptChunk(**kwargs))
    if rows:
        db.add_all(rows)
    db.commit()

    logger.info(
        "RAG_INGEST_SCRIPT script_id=%s chunks=%d embedded=%s",
        script_id, len(rows), "true" if embedded else "false",
    )
    return {"chunks": len(rows), "embedded": embedded}
