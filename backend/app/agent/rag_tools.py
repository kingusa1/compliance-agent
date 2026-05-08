"""L6 agent layer — 5 tools used internally by the L4 /agents/[name]
similar-failure cluster panel and the post-demo chat UI revival.

Tools (Anthropic tool-use schema):
  • query_call(call_id)
  • search_scripts(text, supplier?)
  • find_similar_failures(checkpoint_id)
  • compare_call_to_script(call_id)
  • list_directives_for(agent_name)

NOTE: this module is separate from the existing per-checkpoint analyzer
tool layer in `app.agent.tools` (which exposes find_evidence/verify_quote/
check_speaker/etc). These two layers serve different agent loops.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models import Call, CallCheckpoint, FixDirective, Script
from app.rag import search as rag_search

logger = logging.getLogger(__name__)


# ── Anthropic tool-use schemas ──────────────────────────────────────────────

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "query_call",
        "description": (
            "Load a call by id with its checkpoints, segments, flags, and entities. "
            "Use this first when the question is about a specific call."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"call_id": {"type": "string"}},
            "required": ["call_id"],
        },
    },
    {
        "name": "search_scripts",
        "description": (
            "Semantic search over indexed script checkpoints. Optionally filter by supplier."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "supplier": {"type": "string"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "find_similar_failures",
        "description": (
            "Given a checkpoint id, find similar transcript chunks across all calls. "
            "Use this to surface recurring failure patterns for a checkpoint."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"checkpoint_id": {"type": "string"}},
            "required": ["checkpoint_id"],
        },
    },
    {
        "name": "compare_call_to_script",
        "description": (
            "Compare a call's transcript against its script's expected phrases. "
            "Returns per-checkpoint coverage (matched/missing phrases)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"call_id": {"type": "string"}},
            "required": ["call_id"],
        },
    },
    {
        "name": "list_directives_for",
        "description": (
            "List fix-directives raised on calls handled by a given agent (by agent_name)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"agent_name": {"type": "string"}},
            "required": ["agent_name"],
        },
    },
]


# ── Executors ───────────────────────────────────────────────────────────────


def query_call(db: Session, *, call_id: str) -> dict[str, Any]:
    call = db.query(Call).filter(Call.id == call_id).one_or_none()
    if call is None:
        return {"error": f"call not found: {call_id}"}

    checkpoints = (
        db.query(CallCheckpoint).filter(CallCheckpoint.call_id == call_id).all()
    )

    # Segments / flags / entities are L2 tables — load defensively in case
    # this branch lacks them.
    segments: list[dict] = []
    flags: list[dict] = []
    entities: list[dict] = []
    try:
        from app.models import CallSegment, Flag, ExtractedEntity

        segments = [
            {"idx": s.idx, "stage": s.stage, "speaker": s.speaker,
             "start_s": float(s.start_s) if s.start_s is not None else None,
             "end_s": float(s.end_s) if s.end_s is not None else None,
             "transcript_excerpt": s.transcript_excerpt}
            for s in db.query(CallSegment).filter(CallSegment.call_id == call_id).all()
        ]
        flags = [
            {"id": str(f.id), "rule_id": f.rule_id, "severity": f.severity,
             "reason": f.reason, "evidence": f.evidence, "risk_tag": f.risk_tag}
            for f in db.query(Flag).filter(Flag.call_id == call_id).all()
        ]
        entities = [
            {"key": e.key, "value": e.value, "source": e.source,
             "confidence": float(e.confidence) if e.confidence is not None else None}
            for e in db.query(ExtractedEntity).filter(ExtractedEntity.call_id == call_id).all()
        ]
    except Exception as e:  # noqa: BLE001
        logger.debug("query_call: optional L2 tables unavailable: %s", e)

    return {
        "call": {
            "id": call.id,
            "filename": call.filename,
            "status": call.status,
            "compliant": call.compliant,
            "score": call.score,
            "agent_name": call.agent_name,
            "customer_name": call.customer_name,
            "detected_supplier": call.detected_supplier,
            "duration_seconds": call.duration_seconds,
        },
        "checkpoints": [
            {
                "id": cp.id,
                "rule_text": cp.rule_text,
                "passed": cp.passed,
                "excerpt": cp.excerpt,
                "confidence": cp.confidence,
                "needs_review": cp.needs_review,
                "reviewer_verdict": cp.reviewer_verdict,
            }
            for cp in checkpoints
        ],
        "segments": segments,
        "flags": flags,
        "entities": entities,
    }


def search_scripts(db: Session, *, text: str, supplier: str | None = None) -> dict[str, Any]:
    results = rag_search.search(
        query=text, namespace="scripts", supplier=supplier, top_k=10, db=db
    )
    return {"results": [
        {"ref_id": r.ref_id, "text": r.text, "score": r.score, "metadata": r.metadata}
        for r in results
    ]}


def find_similar_failures(db: Session, *, checkpoint_id: str) -> dict[str, Any]:
    cp = db.query(CallCheckpoint).filter(CallCheckpoint.id == checkpoint_id).one_or_none()
    if cp is None:
        return {"error": f"checkpoint not found: {checkpoint_id}"}
    query = cp.rule_text or cp.excerpt or ""
    results = rag_search.search(
        query=query, namespace="transcripts", top_k=5, db=db
    )
    return {
        "checkpoint": {"id": cp.id, "rule_text": cp.rule_text, "passed": cp.passed},
        "results": [
            {"ref_id": r.ref_id, "text": r.text, "score": r.score, "metadata": r.metadata}
            for r in results
        ],
    }


def compare_call_to_script(db: Session, *, call_id: str) -> dict[str, Any]:
    """Diff each script checkpoint's expected_phrases against the call transcript.

    Uses `app.word_match.find_word_range` when available — falls back to a
    simple substring presence check otherwise.
    """
    call = db.query(Call).filter(Call.id == call_id).one_or_none()
    if call is None:
        return {"error": f"call not found: {call_id}"}

    transcript = (
        call.assemblyai_transcript or call.gemini_transcript or call.transcript or ""
    )
    if not transcript or not call.script_id:
        return {"call_id": call_id, "checkpoints": [], "missing_data": True}

    script = db.query(Script).filter(Script.id == call.script_id).one_or_none()
    if script is None:
        return {"error": f"script not found: {call.script_id}"}

    try:
        checkpoints = json.loads(script.checkpoints or "[]")
    except Exception:
        checkpoints = []

    word_data: list[dict] = []
    if call.word_data:
        try:
            word_data = json.loads(call.word_data) if isinstance(call.word_data, str) else call.word_data
        except Exception:
            word_data = []

    try:
        from app.word_match import find_word_range
    except Exception:
        find_word_range = None  # type: ignore

    results: list[dict] = []
    transcript_lower = transcript.lower()
    for cp in checkpoints:
        if not isinstance(cp, dict):
            continue
        name = cp.get("name") or ""
        phrases = cp.get("expected_phrases") or cp.get("key_phrases") or []
        matched: list[str] = []
        missing: list[str] = []
        for ph in phrases:
            if not ph:
                continue
            found = False
            if find_word_range is not None and word_data:
                a, b = find_word_range(ph, word_data)
                found = a is not None
            if not found:
                found = ph.lower() in transcript_lower
            (matched if found else missing).append(ph)
        results.append({
            "name": name,
            "matched": matched,
            "missing": missing,
            "coverage": (len(matched) / len(phrases)) if phrases else None,
        })
    return {"call_id": call_id, "script_id": script.id, "checkpoints": results}


def list_directives_for(db: Session, *, agent_name: str) -> dict[str, Any]:
    rows = (
        db.query(FixDirective, Call)
        .join(Call, FixDirective.call_id == Call.id)
        .filter(Call.agent_name == agent_name)
        .order_by(FixDirective.created_at.desc())
        .all()
    )
    return {
        "agent_name": agent_name,
        "directives": [
            {
                "id": str(d.id),
                "call_id": d.call_id,
                "title": d.title,
                "status": d.status,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d, _ in rows
        ],
    }


# ── Dispatcher ──────────────────────────────────────────────────────────────


_HANDLERS = {
    "query_call": query_call,
    "search_scripts": search_scripts,
    "find_similar_failures": find_similar_failures,
    "compare_call_to_script": compare_call_to_script,
    "list_directives_for": list_directives_for,
}


def dispatch(db: Session, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Route a tool call from the LLM to the right handler."""
    handler = _HANDLERS.get(name)
    if handler is None:
        return {"error": f"unknown tool: {name}"}
    try:
        return handler(db, **arguments)
    except TypeError as e:
        return {"error": f"bad arguments for {name}: {e}"}
    except Exception as e:  # noqa: BLE001
        return {"error": f"tool {name} raised: {type(e).__name__}: {e}"}
