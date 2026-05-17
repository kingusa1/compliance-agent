"""Observability proxy for the durable workflow.

Three endpoints surface workflow state to the frontend WITHOUT the browser
talking to Inngest directly:

    GET /api/observability/runs           — list with linked call enrichment
    GET /api/observability/runs/{run_id}  — run detail + steps + i/o JSON
    GET /api/observability/stream         — SSE feed: heartbeat + run deltas

Why a proxy and not direct: Inngest's :8288 dashboard is the engine view; we
also need the BUSINESS view (which call this run was for, which customer,
which deal). That join lives in our DB. We pull both, merge, and serve.

Degraded mode: when Inngest is unreachable the endpoints stay 200 with
`inngest_status: "unreachable"` and an empty runs list, so the frontend
doesn't crash on a stale dev session.

Inngest's REST surface is GraphQL at /v0/gql (the dashboard's own API). The
relevant queries are:
    runs(first, filter, orderBy)              — list
    run(runID).trace.childrenSpans            — step boundaries
    runTrigger(runID).payloads                — trigger event for call_id
    runTraceSpanOutputByID(outputID)          — per-step input/output JSON
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta
from app._clock import utcnow
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AgentTrace, Call, CallCheckpoint, CustomerDeal, FailedJob, PipelineStepLog


log = logging.getLogger(__name__)

INNGEST_API_URL = os.environ.get("INNGEST_API_URL", "http://127.0.0.1:8288")
INNGEST_TIMEOUT_S = float(os.environ.get("INNGEST_API_TIMEOUT", "3"))
JSON_TRUNCATE_BYTES = 4096


observability_router = APIRouter(prefix="/api/observability", tags=["observability"])


# ── GraphQL plumbing ─────────────────────────────────────────────────────

async def _gql(query: str, variables: dict | None = None) -> dict | None:
    """POST a query to Inngest's /v0/gql. Returns parsed `data` dict on
    success, None on any failure (timeout, connection refused, GraphQL
    errors). Caller is expected to fall through to degraded mode.
    """
    payload: dict[str, Any] = {"query": query}
    if variables:
        payload["variables"] = variables
    try:
        async with httpx.AsyncClient(timeout=INNGEST_TIMEOUT_S) as c:
            r = await c.post(f"{INNGEST_API_URL}/v0/gql", json=payload)
            r.raise_for_status()
            body = r.json()
    except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError) as e:
        log.warning(f"OBSERVABILITY inngest unreachable: {type(e).__name__}: {e}")
        return None
    except Exception as e:
        log.warning(f"OBSERVABILITY inngest unexpected error: {e!r}")
        return None
    if "errors" in body:
        log.warning(f"OBSERVABILITY graphql errors: {body['errors']}")
        return None
    return body.get("data")


# ── shape adapters ───────────────────────────────────────────────────────

_RUNS_LIST_QUERY = """
query Runs($first: Int!, $filter: RunsFilterV2!, $orderBy: [RunsV2OrderBy!]!) {
  runs(first: $first, filter: $filter, orderBy: $orderBy) {
    edges {
      node {
        id
        status
        startedAt
        endedAt
        eventName
        function { name slug }
        triggerIDs
      }
    }
  }
}
"""

_RUN_DETAIL_QUERY = """
query RunDetail($runID: String!) {
  run(runID: $runID) {
    id
    status
    startedAt
    endedAt
    eventName
    function { name slug }
    trace {
      spanID
      name
      status
      startedAt
      endedAt
      childrenSpans {
        spanID
        name
        status
        startedAt
        endedAt
        outputID
        attempts
      }
    }
  }
  runTrigger(runID: $runID) {
    eventName
    payloads
  }
}
"""

_RUN_TRIGGER_QUERY = """
query RunTrigger($runID: String!) {
  runTrigger(runID: $runID) {
    eventName
    payloads
  }
}
"""

_SPAN_OUTPUT_QUERY = """
query SpanOutput($outputID: String!) {
  runTraceSpanOutputByID(outputID: $outputID) {
    input
    data
    error { message stack }
  }
}
"""


def _ms_between(a: str | None, b: str | None) -> int | None:
    """Inclusive ms between two ISO timestamps; None if either missing."""
    if not a or not b:
        return None
    try:
        ta = datetime.fromisoformat(a.replace("Z", "+00:00"))
        tb = datetime.fromisoformat(b.replace("Z", "+00:00"))
    except ValueError:
        return None
    return int((tb - ta).total_seconds() * 1000)


def _normalize_status(raw: str | None) -> str:
    """Inngest uses COMPLETED / RUNNING / FAILED / CANCELLED uppercase; the
    frontend wants lowercase succeeded/running/failed/cancelled.
    """
    if not raw:
        return "unknown"
    m = {
        "COMPLETED": "succeeded",
        "RUNNING": "running",
        "FAILED": "failed",
        "CANCELLED": "cancelled",
        "QUEUED": "running",
    }
    return m.get(raw.upper(), raw.lower())


def _truncate_json(blob: str | None) -> tuple[Any, bool]:
    """Take a JSON string from Inngest, parse it, and trim if oversized.
    Returns (parsed_or_string, truncated_flag).
    """
    if blob is None:
        return None, False
    if len(blob) > JSON_TRUNCATE_BYTES:
        # Keep raw bytes for the frontend to display; mark truncated.
        return {"_truncated": True, "_preview": blob[:JSON_TRUNCATE_BYTES]}, True
    try:
        return json.loads(blob), False
    except (json.JSONDecodeError, TypeError):
        return blob, False


# ── DB enrichment ────────────────────────────────────────────────────────

def _extract_call_id_from_payloads(payloads: list[str] | None) -> str | None:
    """Trigger payloads is a JSON-string array; pull `data.call_id` out of
    the first one. Returns None if anything's malformed.
    """
    if not payloads:
        return None
    try:
        first = json.loads(payloads[0])
        return (first.get("data") or {}).get("call_id")
    except (json.JSONDecodeError, TypeError, KeyError):
        return None


def _enrich_links(db: Session, call_ids: list[str]) -> dict[str, dict]:
    """One DB round-trip for all call_ids; returns {call_id: {customer_name, deal_id}}."""
    if not call_ids:
        return {}
    rows = (
        db.query(Call.id, Call.customer_name, Call.deal_id, CustomerDeal.customer_name.label("deal_customer_name"))
        .outerjoin(CustomerDeal, Call.deal_id == CustomerDeal.id)
        .filter(Call.id.in_(call_ids))
        .all()
    )
    out: dict[str, dict] = {}
    for r in rows:
        out[str(r.id)] = {
            "customer_name": r.deal_customer_name or r.customer_name,
            "deal_id": str(r.deal_id) if r.deal_id else None,
        }
    return out


def _best_transcript(call: Call) -> tuple[str | None, str | None]:
    """Pick the best available transcript and the provider that produced it."""
    candidates = [
        ("AssemblyAI Universal-3 Pro", call.assemblyai_transcript),
        ("Cohere transcribe-03", call.cohere_transcript),
        ("Gemini", call.gemini_transcript),
        ("Whisper-Large-v3 (Groq)", call.groq_whisper_transcript),
        ("Deepgram Nova-3", call.transcript),
    ]
    for label, body in candidates:
        if body and body.strip():
            return body, label
    return None, None


def _enrich_step(db: Session, step_name: str | None, call_id: str | None) -> dict | None:
    """Pull the rich, business-side evidence for one workflow step.

    The Inngest step return values are intentionally tiny (a status
    pointer like {"source": "assemblyai"}) so the persisted SQLite
    doesn't bloat. The actual evidence — transcript text, LLM prompts,
    checkpoint verdicts — lives in our Postgres tables. This helper
    joins the step name to the relevant rows so the drawer can show a
    compliance auditor what they actually need to see.
    """
    if not step_name or not call_id:
        return None

    name = step_name.lower()
    call = db.query(Call).filter(Call.id == call_id).one_or_none()
    if not call:
        return None

    if "download_audio" in name:
        return {
            "filename": call.filename,
            "file_size": call.file_size,
            "duration_seconds": call.duration_seconds,
            "audio_storage_key": call.audio_storage_key,
        }

    if "transcribe" in name:
        transcript, provider = _best_transcript(call)
        word_count = len(transcript.split()) if transcript else 0
        return {
            "provider": provider,
            "duration_seconds": call.duration_seconds,
            "word_count": word_count,
            "transcript": transcript,
        }

    if "detect_metadata" in name:
        return {
            "customer_name": call.customer_name,
            "agent_name": call.agent_name,
            "detected_supplier": call.detected_supplier,
            "deal_id": str(call.deal_id) if call.deal_id else None,
        }

    if "analyze_checkpoints" in name:
        cps = (
            db.query(CallCheckpoint)
            .filter(CallCheckpoint.call_id == call_id)
            .order_by(CallCheckpoint.id)
            .all()
        )
        traces = (
            db.query(AgentTrace)
            .filter(AgentTrace.call_id == call_id)
            .order_by(AgentTrace.run_id, AgentTrace.turn)
            .all()
        )
        # Group LLM turns by run_id so the drawer can show one
        # prompt → response pair per batch the agent processed.
        turns_by_run: dict[str, list[dict]] = {}
        for t in traces:
            turns_by_run.setdefault(t.run_id, []).append({
                "turn": t.turn,
                "role": t.role,
                "tool_name": t.tool_name,
                "content": (t.content or "")[:4000],
                "model": t.model,
            })
        return {
            "checkpoints": [
                {
                    "rule_text": cp.rule_text,
                    "passed": cp.passed,
                    "confidence": cp.confidence,
                    "needs_review": cp.needs_review,
                    "excerpt": cp.excerpt,
                }
                for cp in cps
            ],
            "agent_runs": [
                {"run_id": rid, "turns": turns}
                for rid, turns in turns_by_run.items()
            ],
        }

    if "score" in name or "finalize" in name:
        return {
            "score": call.score,
            "compliant": call.compliant,
            "reason": call.reason,
            "status": call.status,
        }

    return None


# ── route 1: runs list ───────────────────────────────────────────────────

@observability_router.get("/runs")
async def list_runs(
    workflow: str | None = Query(default=None),
    status: str | None = Query(default=None, description="running|succeeded|failed|cancelled"),
    since: str | None = Query(default=None, description="ISO timestamp; default last 30d"),
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict:
    """List recent runs with linked call/customer/deal metadata.

    Default window is 30d so the page surfaces the user's recent
    historical runs, not just the last hour. The frontend exposes
    1h/24h/7d/all switches for narrower filtering.
    """
    from_iso = since or (utcnow() - timedelta(days=30)).isoformat() + "Z"
    if not from_iso.endswith("Z") and "+" not in from_iso:
        from_iso += "Z"

    inngest_status_filter: list[str] = []
    if status:
        m = {"running": "RUNNING", "succeeded": "COMPLETED", "failed": "FAILED", "cancelled": "CANCELLED"}
        if status in m:
            inngest_status_filter = [m[status]]

    filter_arg: dict[str, Any] = {"from": from_iso, "timeField": "QUEUED_AT"}
    if inngest_status_filter:
        filter_arg["status"] = inngest_status_filter

    data = await _gql(
        _RUNS_LIST_QUERY,
        {
            "first": limit,
            "filter": filter_arg,
            "orderBy": [{"field": "QUEUED_AT", "direction": "DESC"}],
        },
    )

    if data is None:
        # Inngest unreachable (DISABLE_INNGEST_EMIT=1 dev path). Fall back to
        # synthesizing run rows from recent Call table records so the
        # /observability page still shows live + completed calls — the
        # legacy in-process pipeline writes step_log rows we can drill into.
        # Project only the columns we need + cap to last 7 days; stops the
        # Supabase pooler from pulling MB of transcript/word_data on every
        # 5s poll, which made the page feel sluggish.
        from sqlalchemy import select  # local import — avoid top-level churn
        recent_calls = (
            db.execute(
                select(
                    Call.id, Call.created_at, Call.completed_at,
                    Call.status, Call.customer_name, Call.deal_id,
                )
                .where(Call.created_at >= utcnow() - timedelta(days=7))
                .order_by(Call.created_at.desc())
                .limit(min(limit, 100))
            ).all()
        )
        # 2026-05-18 audit fix: missing entries fell through to default
        # "running", which left calls that finished as needs_manual_review
        # showing as a 4.7h-stuck pipeline run in the observability page.
        # Anything terminal (completed, failed, reviewer-decided, cancelled)
        # maps to a terminal pipeline state; only the actively-processing
        # status maps to "running".
        synth_status_map = {
            "completed": "succeeded",
            "needs_manual_review": "succeeded",
            "processing": "running",
            "queued": "running",
            "pending_audio": "running",
            "failed": "failed",
            "processing_failed": "failed",
            "cancelled": "cancelled",
        }
        runs_out = []
        for c in recent_calls:
            cs = synth_status_map.get((c.status or "").lower(), "succeeded")
            if status and status != cs:
                continue
            runs_out.append({
                "run_id": f"local:{c.id}",
                "run_url": None,
                "workflow": "process_call",
                "workflow_name": "process_call",
                "started_at": c.created_at.isoformat() if c.created_at else None,
                "finished_at": c.completed_at.isoformat() if c.completed_at else None,
                "duration_ms": _ms_between(
                    c.created_at.isoformat() if c.created_at else None,
                    c.completed_at.isoformat() if c.completed_at else None,
                ),
                "status": cs,
                "trigger_event_name": "call/uploaded",
                "call_id": c.id,
                "linked": {"call_id": c.id, "customer_name": c.customer_name, "deal_id": str(c.deal_id) if c.deal_id else None},
            })
        return {"runs": runs_out, "inngest_status": "fallback_local"}

    edges = (data.get("runs") or {}).get("edges") or []

    # First pass: gather call_ids for one batched DB enrichment query.
    runs_raw: list[tuple[dict, str | None]] = []
    call_ids: list[str] = []
    for edge in edges:
        node = edge.get("node") or {}
        run_id = node.get("id")
        if not run_id:
            continue
        # Pull trigger payloads for this run to extract call_id (one extra
        # GQL call per run — acceptable for dev volumes).
        trig = await _gql(_RUN_TRIGGER_QUERY, {"runID": run_id})
        trigger_obj = (trig or {}).get("runTrigger") or {}
        payloads = trigger_obj.get("payloads")
        cid = _extract_call_id_from_payloads(payloads)
        if cid:
            call_ids.append(cid)
        runs_raw.append((node, cid))

    if workflow:
        runs_raw = [(n, c) for (n, c) in runs_raw if (n.get("function") or {}).get("slug") == workflow or (n.get("function") or {}).get("name") == workflow]

    enrichment = _enrich_links(db, list({c for c in call_ids if c}))

    runs_out = []
    for node, cid in runs_raw:
        link = enrichment.get(cid, {}) if cid else {}
        runs_out.append({
            "run_id": node.get("id"),
            "workflow_name": (node.get("function") or {}).get("name"),
            "started_at": node.get("startedAt"),
            "finished_at": node.get("endedAt"),
            "duration_ms": _ms_between(node.get("startedAt"), node.get("endedAt")),
            "status": _normalize_status(node.get("status")),
            "trigger_event_name": node.get("eventName") or "call/uploaded",
            "linked": {
                "call_id": cid,
                "customer_name": link.get("customer_name"),
                "deal_id": link.get("deal_id"),
            },
        })
    return {"runs": runs_out, "inngest_status": "ok"}


# ── route 1b: orphans — calls without a workflow run ─────────────────────

@observability_router.get("/orphans")
async def list_orphans(
    older_than_seconds: int = Query(default=30, ge=0, le=3600),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    """Calls in status=processing whose workflow event never landed in
    Inngest. Surfaces invisible bugs where Inngest was unhealthy at upload
    time and a call quietly stalls forever — gives the reviewer something
    to retry instead of a frozen row in /calls.

    The cross-check: pull the last 200 Inngest run.call_id pairs, then
    query our DB for processing calls older than `older_than_seconds`
    that aren't in that set. 30s default is a generous lower bound — a
    healthy run reaches Inngest's runs list within a few seconds.
    """
    from datetime import datetime, timedelta

    cutoff = utcnow() - timedelta(seconds=older_than_seconds)
    candidates = (
        db.query(Call)
        .filter(Call.status == "processing", Call.created_at < cutoff)
        .order_by(Call.created_at.desc())
        .limit(limit)
        .all()
    )
    if not candidates:
        return {"orphans": [], "inngest_status": "ok"}

    # Pull the recent runs from Inngest and map each to its call_id so we
    # can subtract them from candidates.
    data = await _gql(
        _RUNS_LIST_QUERY,
        {
            "first": 200,
            "filter": {
                "from": (utcnow() - timedelta(days=30)).isoformat() + "Z",
                "timeField": "QUEUED_AT",
            },
            "orderBy": [{"field": "QUEUED_AT", "direction": "DESC"}],
        },
    )
    inngest_status = "ok" if data is not None else "unreachable"

    known_call_ids: set[str] = set()
    if data:
        for edge in (data.get("runs") or {}).get("edges", []) or []:
            node = edge.get("node") or {}
            run_id = node.get("id")
            if not run_id:
                continue
            trig = await _gql(_RUN_TRIGGER_QUERY, {"runID": run_id}) or {}
            cid = _extract_call_id_from_payloads((trig.get("runTrigger") or {}).get("payloads"))
            if cid:
                known_call_ids.add(cid)

    customer_name_for = _enrich_links(db, [c.id for c in candidates])
    orphans = [
        {
            "call_id": c.id,
            "filename": c.filename,
            "customer_name": (customer_name_for.get(c.id) or {}).get("customer_name") or c.customer_name,
            "deal_id": str(c.deal_id) if c.deal_id else None,
            "call_type": c.call_type,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "age_seconds": int((utcnow() - c.created_at).total_seconds()) if c.created_at else None,
        }
        for c in candidates
        if c.id not in known_call_ids
    ]
    return {"orphans": orphans, "inngest_status": inngest_status}


@observability_router.post("/orphans/{call_id}/redispatch")
async def redispatch_orphan(call_id: str, db: Session = Depends(get_db)) -> dict:
    """Re-emit the call/uploaded event so Inngest picks the call up.

    Idempotent in practice: Inngest creates a fresh run on each event;
    the workflow itself wipes prior CallCheckpoint rows for the call_id
    before inserting new ones, so re-running yields the same final state
    rather than duplicates.
    """
    import inngest

    from app.inngest_client import inngest_client
    from app.workflows.events import CALL_UPLOADED

    call = db.query(Call).filter(Call.id == call_id).one_or_none()
    if not call:
        raise HTTPException(404, "call not found")

    await inngest_client.send(
        inngest.Event(
            name=CALL_UPLOADED,
            data={
                "call_id": str(call.id),
                "audio_path": call.file_path,
                "customer_name": call.customer_name,
                "deal_id": str(call.deal_id) if call.deal_id else None,
                "call_type": call.call_type,
                "script_id": call.script_id,
            },
        )
    )
    log.info(f"OBSERVABILITY orphan redispatched call_id={call_id}")
    return {"ok": True, "call_id": call_id}


# ── route 2: run detail ──────────────────────────────────────────────────

@observability_router.get("/runs/{run_id}")
async def get_run_detail(
    run_id: str,
    full: bool = Query(default=False, description="bypass step JSON truncation"),
    db: Session = Depends(get_db),
) -> dict:
    """Return run + steps + per-step input/output JSON (truncated to 4KB
    unless ?full=1)."""
    data = await _gql(_RUN_DETAIL_QUERY, {"runID": run_id})
    if data is None:
        raise HTTPException(status_code=503, detail={"error": "inngest_unreachable", "run_id": run_id})

    run_node = data.get("run")
    if not run_node:
        raise HTTPException(status_code=404, detail={"error": "run_not_found", "run_id": run_id})

    trig = data.get("runTrigger") or {}
    cid = _extract_call_id_from_payloads(trig.get("payloads"))
    enrichment = _enrich_links(db, [cid] if cid else [])
    link = enrichment.get(cid, {}) if cid else {}

    # Fetch outputs for all child spans (sequentially — small fanout).
    children = ((run_node.get("trace") or {}).get("childrenSpans") or [])
    steps: list[dict] = []
    for span in children:
        out = None
        if span.get("outputID"):
            out_data = await _gql(_SPAN_OUTPUT_QUERY, {"outputID": span["outputID"]})
            if out_data:
                out = out_data.get("runTraceSpanOutputByID")
        input_blob = (out or {}).get("input")
        data_blob = (out or {}).get("data")
        err = (out or {}).get("error")

        input_parsed, input_truncated = _truncate_json(input_blob if not full else input_blob)
        output_parsed, output_truncated = _truncate_json(data_blob if not full else data_blob)
        if full:
            input_parsed = input_blob and json.loads(input_blob) if isinstance(input_blob, str) and input_blob.strip().startswith(("{", "[")) else input_blob
            output_parsed = data_blob and json.loads(data_blob) if isinstance(data_blob, str) and data_blob.strip().startswith(("{", "[")) else data_blob

        steps.append({
            "name": span.get("name"),
            "status": _normalize_status(span.get("status")),
            "started_at": span.get("startedAt"),
            "finished_at": span.get("endedAt"),
            "duration_ms": _ms_between(span.get("startedAt"), span.get("endedAt")),
            "attempt": (span.get("attempts") or 1),
            "input_json": input_parsed,
            "input_truncated": input_truncated,
            "output_json": output_parsed,
            "output_truncated": output_truncated,
            "error": {"message": err.get("message"), "stack": err.get("stack")} if err else None,
            "evidence": _enrich_step(db, span.get("name"), cid),
        })

    return {
        "run": {
            "run_id": run_node.get("id"),
            "workflow_name": (run_node.get("function") or {}).get("name"),
            "started_at": run_node.get("startedAt"),
            "finished_at": run_node.get("endedAt"),
            "duration_ms": _ms_between(run_node.get("startedAt"), run_node.get("endedAt")),
            "status": _normalize_status(run_node.get("status")),
            "trigger_event_name": trig.get("eventName") or run_node.get("eventName"),
            "linked": {
                "call_id": cid,
                "customer_name": link.get("customer_name"),
                "deal_id": link.get("deal_id"),
            },
        },
        "steps": steps,
        "inngest_status": "ok",
    }


# ── route 3: SSE stream ──────────────────────────────────────────────────

@observability_router.get("/stream")
async def stream_runs(request: Request) -> StreamingResponse:
    """SSE feed of run/step events.

    Implementation: 1-second polling against /runs internally, emitting
    deltas as `event: run.started|run.finished` / `data: {...}` blocks. A
    `:keep-alive` comment fires every 5s so proxies / browsers don't time
    the connection out.
    """
    async def _stream():
        seen: dict[str, str] = {}  # run_id → status
        last_keepalive = time.time()
        # First emit a hello so clients see ANYTHING within 50ms even if
        # there are no runs in flight (the contract requires heartbeat
        # within 5s).
        yield ": connected\n\n"
        while True:
            # Break the loop on client disconnect — without this the
            # generator polls Inngest forever per orphan tab, holding open
            # backend TCP connections until the browser hits its per-host
            # limit (~6) and stalls every other fetch on localhost:8001.
            # Surfaced during D05 gate runs (12 stale ComplianceAI tabs
            # → 19 ESTABLISHED sockets → fetch hang).
            if await request.is_disconnected():
                log.info("OBSERVABILITY stream client disconnected, closing")
                return
            now = time.time()
            if now - last_keepalive > 5:
                yield ": keep-alive\n\n"
                last_keepalive = now
            data = await _gql(
                _RUNS_LIST_QUERY,
                {
                    "first": 20,
                    "filter": {
                        "from": (utcnow() - timedelta(minutes=5)).isoformat() + "Z",
                        "timeField": "QUEUED_AT",
                    },
                    "orderBy": [{"field": "QUEUED_AT", "direction": "DESC"}],
                },
            )
            if data is not None:
                edges = (data.get("runs") or {}).get("edges") or []
                for edge in edges:
                    node = edge.get("node") or {}
                    rid = node.get("id")
                    new_status = _normalize_status(node.get("status"))
                    if not rid:
                        continue
                    prev = seen.get(rid)
                    if prev is None:
                        evt_type = "run.started"
                    elif prev != new_status and new_status in ("succeeded", "failed", "cancelled"):
                        evt_type = "run.finished"
                    else:
                        continue
                    payload = {
                        "event_type": evt_type,
                        "payload": {
                            "run_id": rid,
                            "workflow_name": (node.get("function") or {}).get("name"),
                            "status": new_status,
                            "started_at": node.get("startedAt"),
                            "finished_at": node.get("endedAt"),
                        },
                    }
                    yield f"event: {evt_type}\ndata: {json.dumps(payload)}\n\n"
                    seen[rid] = new_status
            await asyncio.sleep(1)

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


# ── route 4: stuck runs (Pillar 1 — Durability) ──────────────────────────

_STUCK_RUNS_QUERY = text(
    """
    SELECT id, customer_name, last_step_name, last_step_started_at,
           last_step_error, COALESCE(watchdog_redispatch_count, 0) AS retry_count,
           EXTRACT(EPOCH FROM (NOW() - last_step_started_at))::int AS stuck_seconds
    FROM calls
    WHERE last_step_started_at < (NOW() - INTERVAL '7 minutes')
      AND completed_at IS NULL
      AND status NOT IN ('completed', 'failed')
    ORDER BY last_step_started_at ASC
    LIMIT 50
    """
)


@observability_router.get("/stuck")
async def list_stuck_runs(db: Session = Depends(get_db)) -> dict:
    """Calls whose current step has been running > 7 minutes without
    completing. Surfaces the same population the redispatch-watchdog cron
    targets so the UI can show *why* a call is sitting still and whether
    the watchdog has already retried it once.
    """
    try:
        rows = db.execute(_STUCK_RUNS_QUERY).fetchall()
    except Exception as e:  # noqa: BLE001 — degrade gracefully on missing cols
        log.warning(f"OBSERVABILITY stuck endpoint query failed: {e!r}")
        return {"stuck": []}
    out = [
        {
            "call_id": str(r.id),
            "customer_name": r.customer_name,
            "last_step_name": r.last_step_name,
            "stuck_for_seconds": int(r.stuck_seconds) if r.stuck_seconds is not None else 0,
            "retry_count": int(r.retry_count or 0),
            "last_error": r.last_step_error,
        }
        for r in rows
    ]
    return {"stuck": out}


@observability_router.get("/audit")
def list_audit(
    limit: int = Query(50, ge=1, le=500),
    action: str | None = None,
    actor_id: str | None = None,
    db: Session = Depends(get_db),
):
    """Recent audit_log rows. Hash chain integrity is preserved by the writer
    (app/audit.record_audit); this route only reads."""
    q = (
        "SELECT id, occurred_at, organization_id, actor_id, action, "
        "       entity_type, entity_id, payload, prev_hash, this_hash "
        "FROM audit_log WHERE 1=1"
    )
    params: dict[str, object] = {"limit": limit}
    if action:
        q += " AND action = :action"
        params["action"] = action
    if actor_id:
        q += " AND actor_id = :actor"
        params["actor"] = actor_id
    q += " ORDER BY occurred_at DESC, id DESC LIMIT :limit"
    rows = [dict(r._mapping) for r in db.execute(text(q), params).fetchall()]
    return {"rows": rows}


@observability_router.get("/failed-jobs")
def list_failed_jobs(
    limit: int = Query(50, ge=1, le=500),
    call_id: str | None = None,
    db: Session = Depends(get_db),
):
    """Recent Inngest-exhausted runs. Reviewer UI surfaces these for replay."""
    q = db.query(FailedJob).order_by(FailedJob.exhausted_at.desc())
    if call_id:
        q = q.filter(FailedJob.call_id == call_id)
    rows = [
        {
            "id": r.id,
            "call_id": r.call_id,
            "last_step": r.last_step,
            "attempts": r.attempts,
            "last_error": r.last_error,
            "exhausted_at": r.exhausted_at.isoformat() if r.exhausted_at else None,
        }
        for r in q.limit(limit).all()
    ]
    return {"rows": rows}


# ── routes 8 + 9: per-call pipeline flow viz (n8n-equivalent) ────────────
# /runs/{call_id}/steps  → ordered pipeline_step_log rows for the step-waterfall drawer
# /runs/{call_id}/feed   → merged chronological log: pipeline_step_log + agent_traces
#                          rows so the ComfyUI-style terminal widget can show every
#                          step start/ok/err line + every LLM prompt/response.
# Both are polled by the frontend every 2s; SSE upgrade is a future improvement.

@observability_router.get("/runs/{call_id}/steps")
async def get_run_steps(
    call_id: str,
    db: Session = Depends(get_db),
) -> dict:
    """Ordered pipeline_step_log rows for one call. Powers the
    /observability drawer's step waterfall (replaces the mocked array).
    """
    rows = (
        db.query(PipelineStepLog)
        .filter(PipelineStepLog.call_id == call_id)
        .order_by(PipelineStepLog.started_at.asc())
        .all()
    )
    return {
        "call_id": call_id,
        "steps": [
            {
                "id": str(r.id),
                "step_name": r.step_name,
                "status": r.status,
                "payload_in": r.payload_in,
                "payload_out": r.payload_out,
                "error_message": r.error_message,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "ended_at": r.ended_at.isoformat() if r.ended_at else None,
                "duration_ms": r.duration_ms,
            }
            for r in rows
        ],
    }


@observability_router.get("/runs/{call_id}/feed")
async def get_run_feed(
    call_id: str,
    since: str | None = Query(default=None, description="ISO timestamp — return only events after"),
    limit: int = Query(default=500, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> dict:
    """Merged chronological log of pipeline_step_log + agent_traces.

    Each event has a uniform shape:
      { kind: "step" | "trace", ts, step_name, role, status, content, ... }
    Powers the ComfyUI-style live terminal widget on /observability.
    """
    from datetime import datetime as _dt

    since_dt = None
    if since:
        try:
            since_dt = _dt.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError:
            since_dt = None

    step_q = db.query(PipelineStepLog).filter(PipelineStepLog.call_id == call_id)
    if since_dt is not None:
        step_q = step_q.filter(PipelineStepLog.started_at > since_dt)
    steps = step_q.order_by(PipelineStepLog.started_at.asc()).limit(limit).all()

    trace_q = db.query(AgentTrace).filter(AgentTrace.call_id == call_id)
    if since_dt is not None:
        trace_q = trace_q.filter(AgentTrace.created_at > since_dt)
    traces = trace_q.order_by(AgentTrace.created_at.asc(), AgentTrace.turn.asc()).limit(limit).all()

    events: list[dict] = []
    for s in steps:
        events.append({
            "kind": "step",
            "ts": s.started_at.isoformat() if s.started_at else None,
            "ended_at": s.ended_at.isoformat() if s.ended_at else None,
            "step_name": s.step_name,
            "status": s.status,
            "duration_ms": s.duration_ms,
            "error_message": s.error_message,
            "payload_in": s.payload_in,
            "payload_out": s.payload_out,
        })
    for t in traces:
        events.append({
            "kind": "trace",
            "ts": t.created_at.isoformat() if t.created_at else None,
            "run_id": t.run_id,
            "turn": t.turn,
            "role": t.role,
            "tool_name": t.tool_name,
            "model": t.model,
            "latency_ms": t.latency_ms,
            "content": (t.content or "")[:8000],
        })
    events.sort(key=lambda e: e.get("ts") or "")

    return {
        "call_id": call_id,
        "count": len(events),
        "events": events,
    }
