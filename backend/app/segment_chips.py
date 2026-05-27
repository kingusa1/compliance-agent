"""Bulk segment-array loader for list endpoints (wave-26, 2026-05-27).

Owner-reported bug:
  /api/customers/{slug} + /api/deals + /api/calls flatten every call to a
  single `call_type: str`, hiding the fact that one audio file can contain
  multiple segments (Lead Gen + Pre-Sales + Verbal + LOA). UI surfaces
  inherit the gap and double-count "required calls", show only the
  dominant segment kind, etc.

This module provides ONE call to fetch segments grouped by call_id with
ZERO N+1 risk, using the json_agg + json_build_object correlated
subquery pattern.

§0 research (agent a50f03bffacc55da8, 2026-05-27):
  - SQLAlchemy 2.0 selectinload requires ORM parents; the existing list
    endpoints use raw text() SQL, so json_agg in a correlated subquery
    is the cleanest path with one round-trip.
  - PostgreSQL official docs confirm ORDER BY inside json_agg is
    deterministic from PG 9.5+ and json_build_object escapes strings
    safely (no injection vector for column-sourced values).
  - COALESCE(..., '[]'::json) is required so calls with zero segments
    return [] not NULL (Pydantic list[Segment] rejects None).

Citations (preserve in the wave commit body):
  - https://www.postgresql.org/docs/current/functions-aggregate.html
  - https://www.postgresql.org/docs/current/functions-json.html
  - https://docs.sqlalchemy.org/en/20/orm/queryguide/relationships.html
  - https://docs.sqlalchemy.org/en/20/orm/queryguide/relationships.html#routing-explicit-joins-statements-into-eagerly-loaded-collections
"""
from __future__ import annotations

import json
from typing import Iterable

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session


class CallSegmentChip(BaseModel):
    """Per-segment chip rendered on customer + deal + calls pages.

    Wave-26 — kept INTENTIONALLY small. The full segment row is at
    /api/calls/{id}/segments; this is the list-page summary.
    """
    kind: str                      # lead_gen | pre_sales | verbal | loa
    score: str | None = None       # "21/26" or None when unscored
    compliant: bool | None = None
    confidence: float | None = None
    idx: int = 0                   # ordering hint for the UI


def fetch_segments_by_call_ids(
    db: Session, call_ids: Iterable[str]
) -> dict[str, list[CallSegmentChip]]:
    """Bulk-load segment chips grouped by call_id, in ONE round-trip.

    Returns ``{call_id: [CallSegmentChip, ...]}``. Calls with zero
    segments are NOT included in the dict; callers should default to
    ``[]`` via ``.get(call_id, [])``. This keeps the helper allocation-
    cheap on pages where most calls are zero-segment (legacy data).

    Postgres path uses the canonical json_agg(... ORDER BY idx) correlated
    subquery. SQLite (tests) falls back to a 2-step fetch + Python group-
    by since SQLite has no json_agg before 3.38 and we target 3.34+.
    """
    ids = list(call_ids)
    if not ids:
        return {}

    bind = db.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        sql = text(
            """
            SELECT c.id AS call_id,
                   COALESCE(
                     (SELECT json_agg(json_build_object(
                          'kind', cs.stage,
                          'score', cs.score,
                          'compliant', cs.compliant,
                          'confidence', cs.confidence,
                          'idx', cs.idx
                       ) ORDER BY cs.idx)
                      FROM call_segments cs
                      WHERE cs.call_id = c.id),
                     '[]'::json
                   ) AS segments
            FROM calls c
            WHERE c.id = ANY(:ids)
            """
        )
        rows = db.execute(sql, {"ids": ids}).fetchall()
        out: dict[str, list[CallSegmentChip]] = {}
        for r in rows:
            payload = r.segments
            if isinstance(payload, str):  # psycopg2 may return str
                payload = json.loads(payload)
            if not payload:
                continue
            out[str(r.call_id)] = [
                CallSegmentChip(
                    kind=str(p.get("kind") or ""),
                    score=p.get("score"),
                    compliant=p.get("compliant"),
                    confidence=float(p["confidence"]) if p.get("confidence") is not None else None,
                    idx=int(p.get("idx") or 0),
                )
                for p in payload
                if p and p.get("kind")
            ]
        return out

    # SQLite fallback (tests). One query, Python group-by. We build
    # numbered placeholders (:id_0, :id_1, ...) inline because SQLite
    # text() doesn't support array binds via :ids the way Postgres does.
    # The placeholder NAMES are static (integer-indexed), never sourced
    # from user input — only the VALUES are bound parameters.
    sql = text(
        f"""
        SELECT cs.call_id, cs.stage, cs.score, cs.compliant, cs.confidence, cs.idx
        FROM call_segments cs
        WHERE cs.call_id IN ({",".join(f":id_{i}" for i in range(len(ids)))})
        ORDER BY cs.call_id, cs.idx
        """
    )
    params = {f"id_{i}": cid for i, cid in enumerate(ids)}
    rows = db.execute(sql, params).fetchall()
    out: dict[str, list[CallSegmentChip]] = {}
    for r in rows:
        out.setdefault(str(r.call_id), []).append(
            CallSegmentChip(
                kind=str(r.stage or ""),
                score=r.score,
                compliant=r.compliant if isinstance(r.compliant, bool) else (
                    None if r.compliant is None else bool(r.compliant)
                ),
                confidence=float(r.confidence) if r.confidence is not None else None,
                idx=int(r.idx or 0),
            )
        )
    # Filter out kind=""
    for cid, segs in list(out.items()):
        out[cid] = [s for s in segs if s.kind]
        if not out[cid]:
            del out[cid]
    return out
