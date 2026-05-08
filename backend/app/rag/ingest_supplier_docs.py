"""Supplier doc ingestion (contract terms, policy docs, T&Cs).

Same shape as ingest_loa but keyed by (supplier, doc_type) so the same
supplier can hold multiple docs (e.g. "contract", "policy", "fee_schedule").
Idempotent rebuild scoped to the (supplier, doc_type) pair.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.rag.chunker import chunk_transcript
from app.rag.ingest_loa import _extract_text  # reuse pdf/md/bytes coercion

log = logging.getLogger(__name__)


def ingest_supplier_docs(
    supplier: str,
    doc_type: str,
    doc_text_or_path: str | Path | bytes,
    db,
) -> int:
    """Chunk + embed + persist a supplier doc. Idempotent on (supplier, doc_type).

    Returns rows written. 0 if SupplierDocChunk ORM is missing.
    """
    try:
        from app.models import SupplierDocChunk  # type: ignore
    except ImportError:
        log.warning(
            "SupplierDocChunk ORM not yet present; ingest_supplier_docs is a no-op stub."
        )
        return 0

    text = _extract_text(doc_text_or_path)
    chunks = chunk_transcript(text, None)
    if not chunks:
        log.info("SUPPLIER_DOC_INGEST supplier=%s doc_type=%s no chunks", supplier, doc_type)
        return 0

    embeddings: list[Any] = [None] * len(chunks)
    try:
        from app.rag.embed import embed_batch

        embeddings = embed_batch([c.text for c in chunks])
    except EnvironmentError as e:
        log.warning("SUPPLIER_DOC embed skipped (no key): %s", e)
    except Exception as e:  # noqa: BLE001
        log.warning("SUPPLIER_DOC embed failed: %s", e)

    db.query(SupplierDocChunk).filter_by(supplier=supplier, doc_type=doc_type).delete()
    for ch, emb in zip(chunks, embeddings):
        db.add(SupplierDocChunk(
            supplier=supplier,
            doc_type=doc_type,
            chunk_idx=ch.chunk_idx,
            text=ch.text,
            embedding=emb,
        ))
    db.commit()

    embedded = embeddings and embeddings[0] is not None
    log.info(
        "SUPPLIER_DOC_INGEST supplier=%s doc_type=%s chunks=%d embedded=%s",
        supplier, doc_type, len(chunks), "yes" if embedded else "no",
    )
    return len(chunks)
