"""LOA template ingestion.

Admin uploads a Letter of Authority PDF (or markdown) per supplier. We
chunk by section / sliding window, embed with the same OpenAI client
used by transcripts, and idempotently rebuild rows for that supplier.

Graceful skip when LoaChunk ORM is not yet registered (main session adds
it after Lane D ships). Graceful skip on missing OPENAI_API_KEY.
"""
from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path
from typing import Any

from app.rag.chunker import chunk_transcript

log = logging.getLogger(__name__)


def _extract_text(doc: str | Path | bytes) -> str:
    """Coerce a path / raw bytes / inline string into plain text."""
    if isinstance(doc, bytes):
        # Try PDF first; fall back to utf-8 decode.
        try:
            from PyPDF2 import PdfReader  # type: ignore

            return "\n".join((p.extract_text() or "") for p in PdfReader(BytesIO(doc)).pages)
        except Exception:
            return doc.decode("utf-8", errors="ignore")

    s = str(doc)
    p = Path(s)
    if p.exists() and p.is_file():
        if p.suffix.lower() == ".pdf":
            try:
                from PyPDF2 import PdfReader  # type: ignore

                return "\n".join((page.extract_text() or "") for page in PdfReader(str(p)).pages)
            except Exception:
                return p.read_bytes().decode("utf-8", errors="ignore")
        return p.read_text(errors="ignore")
    return s


def ingest_loa(supplier: str, doc_text_or_path: str | Path | bytes, db) -> int:
    """Chunk + embed + persist an LOA template for one supplier. Idempotent.

    Returns rows written. 0 if LoaChunk ORM is missing.
    """
    try:
        from app.models import LoaChunk  # type: ignore
    except ImportError:
        log.warning(
            "LoaChunk ORM not yet present; ingest_loa is a no-op stub. "
            "Main session must add the model."
        )
        return 0

    text = _extract_text(doc_text_or_path)
    chunks = chunk_transcript(text, None)  # 3-sentence sliding window
    if not chunks:
        log.info("LOA_INGEST supplier=%s no chunks", supplier)
        return 0

    embeddings: list[Any] = [None] * len(chunks)
    try:
        from app.rag.embed import embed_batch

        embeddings = embed_batch([c.text for c in chunks])
    except EnvironmentError as e:
        log.warning("LOA embed skipped (no key): %s", e)
    except Exception as e:  # noqa: BLE001
        log.warning("LOA embed failed: %s", e)

    db.query(LoaChunk).filter_by(supplier=supplier).delete()
    for ch, emb in zip(chunks, embeddings):
        db.add(LoaChunk(
            supplier=supplier,
            chunk_idx=ch.chunk_idx,
            text=ch.text,
            embedding=emb,
        ))
    db.commit()

    embedded = embeddings and embeddings[0] is not None
    log.info(
        "LOA_INGEST supplier=%s chunks=%d embedded=%s",
        supplier, len(chunks), "yes" if embedded else "no",
    )
    return len(chunks)
