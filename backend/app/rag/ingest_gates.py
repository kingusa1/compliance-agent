"""Compliance gates ingestion.

Reads docs/research/2026-04-25-v2-step-by-step-with-gates.md and splits
on `## Step N — <title>` headings. Each chunk = one step (the prose +
gates listed under that step). Idempotent rebuild — wipes the whole
table and reinserts on every call.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

GATES_DOC_PATH = Path("docs/research/2026-04-25-v2-step-by-step-with-gates.md")
_STEP_HEADING = re.compile(r"^##\s+Step\s+(\d+)\s+[—-]\s+(.+?)$", re.MULTILINE)


def _split_by_step(text: str) -> list[tuple[int, str, str]]:
    """Yield (step_n, title, body) for each ## Step N — Title section."""
    matches = list(_STEP_HEADING.finditer(text))
    if not matches:
        return []
    out: list[tuple[int, str, str]] = []
    for i, m in enumerate(matches):
        step_n = int(m.group(1))
        title = m.group(2).strip()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip()
        out.append((step_n, title, body))
    return out


def ingest_gates(db) -> int:
    """Read the gates doc, chunk per step, embed, write. Idempotent rebuild.

    Returns rows written. 0 if GateChunk ORM missing or doc not found.
    """
    try:
        from app.models import GateChunk  # type: ignore
    except ImportError:
        log.warning("GateChunk ORM not yet present; ingest_gates is a no-op stub.")
        return 0

    doc = GATES_DOC_PATH
    if not doc.exists():
        # Try project-root-relative resolution.
        alt = Path(__file__).resolve().parents[3] / GATES_DOC_PATH
        if alt.exists():
            doc = alt
        else:
            log.warning("GATES_INGEST doc not found at %s", GATES_DOC_PATH)
            return 0

    text = doc.read_text(errors="ignore")
    sections = _split_by_step(text)
    if not sections:
        log.warning("GATES_INGEST no '## Step N —' headings matched")
        return 0

    chunk_texts = [f"Step {n} — {title}\n\n{body}" for (n, title, body) in sections]

    embeddings: list[Any] = [None] * len(chunk_texts)
    try:
        from app.rag.embed import embed_batch

        embeddings = embed_batch(chunk_texts)
    except EnvironmentError as e:
        log.warning("GATES embed skipped (no key): %s", e)
    except Exception as e:  # noqa: BLE001
        log.warning("GATES embed failed: %s", e)

    db.query(GateChunk).delete()
    for (step_n, title, _body), txt, emb in zip(sections, chunk_texts, embeddings):
        db.add(GateChunk(
            step_number=step_n,
            title=title,
            text=txt,
            embedding=emb,
        ))
    db.commit()

    embedded = embeddings and embeddings[0] is not None
    log.info(
        "GATES_INGEST steps=%d embedded=%s",
        len(sections), "yes" if embedded else "no",
    )
    return len(sections)
