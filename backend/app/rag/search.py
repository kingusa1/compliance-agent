"""L6 RAG retrieval: cosine search over transcript_chunks / script_chunks /
agent_learnings. Returns a unified `Result` shape so the API + agent tools
can iterate without caring which namespace produced the row.

Graceful degrade: if OPENAI_API_KEY is unset, returns [] with a warning so
callers can render an "embeddings unavailable" banner.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import AgentLearning, Script
from app.rag.embed import embed_one
from app.rag.namespaces import REGISTRY as _NS_REGISTRY

logger = logging.getLogger(__name__)

# L10: 7 namespaces + 'directives' (legacy agent_learnings) + 'all'.
Namespace = Literal[
    "transcripts",
    "scripts",
    "directives",
    "loa_templates",
    "supplier_docs",
    "gates",
    "rule_catalog",
    "rejections",
    "all",
]


@dataclass
class Result:
    namespace: str
    ref_id: str
    text: str
    score: float
    metadata: dict[str, Any]


def _to_dict(r: Result) -> dict[str, Any]:
    return asdict(r)


def _embed_query(query: str) -> list[float] | None:
    """Embed `query` via OpenAI. Returns None if API key missing."""
    try:
        return embed_one(query)
    except EnvironmentError:
        logger.warning("RAG_SEARCH skipped — OPENAI_API_KEY not set")
        return None
    except Exception as e:  # noqa: BLE001
        logger.warning("RAG_SEARCH embed failed: %s", e)
        return None


def _import_chunk_models():
    try:
        from app.models import TranscriptChunk, ScriptChunk  # type: ignore

        return TranscriptChunk, ScriptChunk
    except Exception:
        return None, None


def _is_postgres(db: Session) -> bool:
    try:
        return db.bind.dialect.name == "postgresql"  # type: ignore[attr-defined]
    except Exception:
        return False


def _format_vec(vec: list[float]) -> str:
    """pgvector accepts vectors as a string literal: '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def _search_transcripts(
    vec: list[float], call_id: str | None, top_k: int, db: Session
) -> list[Result]:
    TranscriptChunk, _ = _import_chunk_models()
    if TranscriptChunk is None or not _is_postgres(db):
        return []
    db.execute(text("SET LOCAL ivfflat.probes = 10"))
    sql = text(
        """
        SELECT id, call_id, chunk_idx, text, speaker, start_s, end_s,
               1 - (embedding <=> CAST(:vec AS vector)) AS score
        FROM transcript_chunks
        WHERE embedding IS NOT NULL
          AND (CAST(:call_id AS text) IS NULL OR call_id = :call_id)
        ORDER BY embedding <=> CAST(:vec AS vector)
        LIMIT :k
        """
    )
    rows = db.execute(sql, {"vec": _format_vec(vec), "call_id": call_id, "k": top_k}).mappings().all()
    return [
        Result(
            namespace="transcripts",
            ref_id=str(r["id"]),
            text=r["text"] or "",
            score=float(r["score"]) if r["score"] is not None else 0.0,
            metadata={
                "call_id": r["call_id"],
                "chunk_idx": r["chunk_idx"],
                "speaker": r["speaker"],
                "start_s": float(r["start_s"]) if r["start_s"] is not None else None,
                "end_s": float(r["end_s"]) if r["end_s"] is not None else None,
            },
        )
        for r in rows
    ]


def _search_scripts(
    vec: list[float], supplier: str | None, top_k: int, db: Session
) -> list[Result]:
    _, ScriptChunk = _import_chunk_models()
    if ScriptChunk is None or not _is_postgres(db):
        return []
    db.execute(text("SET LOCAL ivfflat.probes = 10"))
    sql = text(
        """
        SELECT sc.id, sc.script_id, sc.checkpoint_idx, sc.text,
               s.supplier_name,
               1 - (sc.embedding <=> CAST(:vec AS vector)) AS score
        FROM script_chunks sc
        JOIN scripts s ON s.id = sc.script_id
        WHERE sc.embedding IS NOT NULL
          AND (CAST(:supplier AS text) IS NULL OR s.supplier_name = :supplier)
        ORDER BY sc.embedding <=> CAST(:vec AS vector)
        LIMIT :k
        """
    )
    rows = db.execute(sql, {"vec": _format_vec(vec), "supplier": supplier, "k": top_k}).mappings().all()
    return [
        Result(
            namespace="scripts",
            ref_id=str(r["id"]),
            text=r["text"] or "",
            score=float(r["score"]) if r["score"] is not None else 0.0,
            metadata={
                "script_id": r["script_id"],
                "checkpoint_idx": r["checkpoint_idx"],
                "supplier": r["supplier_name"],
            },
        )
        for r in rows
    ]


def _search_directives(vec: list[float], top_k: int, db: Session) -> list[Result]:
    """Cosine query over agent_learnings.embedding (existing pgvector column)."""
    if not _is_postgres(db):
        return []
    sql = text(
        """
        SELECT id, supplier, checkpoint_name, pattern, lesson,
               1 - (embedding <=> CAST(:vec AS vector)) AS score
        FROM agent_learnings
        WHERE embedding IS NOT NULL
        ORDER BY embedding <=> CAST(:vec AS vector)
        LIMIT :k
        """
    )
    rows = db.execute(sql, {"vec": _format_vec(vec), "k": top_k}).mappings().all()
    return [
        Result(
            namespace="directives",
            ref_id=str(r["id"]),
            text=f"{r['pattern']} → {r['lesson']}",
            score=float(r["score"]) if r["score"] is not None else 0.0,
            metadata={
                "supplier": r["supplier"],
                "checkpoint_name": r["checkpoint_name"],
            },
        )
        for r in rows
    ]


def _resolve_orm(name: str):
    """Look up an ORM class by name on app.models. Returns None if absent."""
    try:
        from app import models as _m

        return getattr(_m, name, None)
    except Exception:
        return None


# L10 generic dispatch for the 5 new namespaces.
# Each entry: (table_name, supplier_filter_col_or_None, metadata_builder)
def _generic_search(
    namespace: str,
    vec: list[float],
    supplier: str | None,
    top_k: int,
    db: Session,
) -> list[Result]:
    """Cosine search over a chunk table registered in namespaces.REGISTRY.

    Skips silently when:
      - namespace not in REGISTRY,
      - ORM class missing (main session hasn't added it yet),
      - dialect != postgres (no pgvector in SQLite test env).
    """
    cfg = _NS_REGISTRY.get(namespace)
    if cfg is None:
        return []
    Orm = _resolve_orm(cfg.table_orm_name)
    if Orm is None or not _is_postgres(db):
        return []

    table = getattr(Orm, "__tablename__", None) or namespace
    cols = {c.name for c in Orm.__table__.columns}  # type: ignore[attr-defined]

    # Pick a supplier filter column when present (loa, supplier_docs, rejections).
    supplier_col = "supplier" if "supplier" in cols else None
    where = ["embedding IS NOT NULL"]
    params: dict[str, Any] = {"vec": _format_vec(vec), "k": top_k}
    if supplier_col and supplier:
        where.append(f"{supplier_col} = :supplier")
        params["supplier"] = supplier

    # ivfflat: with `lists=100` and small tables, default probes=1 misses
    # almost everything. Bump probes for the duration of this query.
    db.execute(text("SET LOCAL ivfflat.probes = 10"))
    sql = text(
        f"""
        SELECT *, 1 - (embedding <=> CAST(:vec AS vector)) AS score
        FROM {table}
        WHERE {' AND '.join(where)}
        ORDER BY embedding <=> CAST(:vec AS vector)
        LIMIT :k
        """
    )
    rows = db.execute(sql, params).mappings().all()

    results: list[Result] = []
    for r in rows:
        # Build a metadata dict from all non-large columns we know about.
        meta: dict[str, Any] = {}
        for k in ("supplier", "doc_type", "step_number", "title",
                  "rule_id", "name", "category", "severity",
                  "agent_name", "fix", "chunk_idx"):
            if k in r.keys():
                meta[k] = r[k]
        results.append(
            Result(
                namespace=namespace,
                ref_id=str(r.get("id") if "id" in r.keys() else r.get("rule_id") or ""),
                text=(r.get("text") or "") if "text" in r.keys() else "",
                score=float(r["score"]) if r["score"] is not None else 0.0,
                metadata=meta,
            )
        )
    return results


def search(
    query: str,
    namespace: Namespace = "all",
    call_id: str | None = None,
    supplier: str | None = None,
    top_k: int = 10,
    db: Session | None = None,
) -> list[Result]:
    """Dispatch a cosine search to one or all namespaces.

    Returns [] if the query can't be embedded (missing key) or pgvector
    is unavailable (e.g. SQLite test env).
    """
    if db is None:
        return []
    vec = _embed_query(query)
    if vec is None:
        return []

    if namespace == "transcripts":
        return _search_transcripts(vec, call_id, top_k, db)
    if namespace == "scripts":
        return _search_scripts(vec, supplier, top_k, db)
    if namespace == "directives":
        return _search_directives(vec, top_k, db)
    if namespace in _NS_REGISTRY and namespace not in ("transcripts", "scripts"):
        return _generic_search(namespace, vec, supplier, top_k, db)

    # 'all' — pull top_k from each, merge by score desc, trim to top_k overall.
    merged: list[Result] = []
    merged.extend(_search_transcripts(vec, call_id, top_k, db))
    merged.extend(_search_scripts(vec, supplier, top_k, db))
    merged.extend(_search_directives(vec, top_k, db))
    for ns in _NS_REGISTRY:
        if ns in ("transcripts", "scripts"):
            continue
        merged.extend(_generic_search(ns, vec, supplier, top_k, db))
    merged.sort(key=lambda r: r.score, reverse=True)
    return merged[:top_k]
