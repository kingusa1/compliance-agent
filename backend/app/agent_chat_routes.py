"""L10 chat-UI revival — POST /api/agent/chat (SSE).

Was CUT in L6 audit-pass-2 per Fix #18; brought back IN scope per L10 design
decision because multi-namespace RAG citing real Watt docs makes the chat
high-value for reviewers on /calls/[id].

Wire-up:
  • Body: {messages: [{role, content}], call_id?: str}
  • Response: text/event-stream
        event: token         data: {"delta": "..."}
        event: tool_call     data: {"name": "...", "input": {...}}
        event: tool_result   data: {"name": "...", "summary": "...", "ok": bool}
        event: citation      data: {"namespace","ref_id","text","score","metadata"}
        event: end           data: {"finish_reason": "...", "iterations": int}

Reuses `app.agent.chat.run_chat` for the tool-use loop (10-iteration cap baked
in there). When `call_id` is provided AND the model calls `query_call`/
`find_similar_failures` without arguments (or with a different id), we still
just dispatch what the model asked for — `call_id` in the body is metadata
the caller wants the agent to know about; the system prompt nudges the agent
to scope its searches to that id.

Mounted by Lane E in main.py. This file does NOT register itself there.
"""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agent import chat as agent_chat
from app.database import get_db

logger = logging.getLogger(__name__)

agent_chat_router = APIRouter(prefix="/api/agent", tags=["agent_chat"])


class _ChatMessage(BaseModel):
    role: str = Field(..., description="user | assistant | system")
    content: str


class ChatRequest(BaseModel):
    messages: list[_ChatMessage]
    call_id: str | None = None


_SYSTEM_PROMPT_TEMPLATE = (
    "You are a compliance reviewer assistant. You help human reviewers audit "
    "energy-broker call transcripts against scripts, supplier LOA templates, "
    "compliance gates, the rule catalog, and past rejections. "
    "Use the available tools to ground every claim in cited source material. "
    "Always prefer citing real evidence over guessing.\n\n"
    "{call_scope}"
    "When citing transcripts include the timestamp; when citing scripts/LOA/"
    "supplier docs include the supplier and section; when citing rules cite "
    "the rule_id."
)


def _build_system_prompt(call_id: str | None) -> str:
    scope = ""
    if call_id:
        scope = (
            f"The reviewer is currently viewing call_id='{call_id}'. "
            "Default to scoping queries to this call (e.g. pass it to "
            "query_call and find_similar_failures).\n\n"
        )
    return _SYSTEM_PROMPT_TEMPLATE.format(call_scope=scope)


def _summarize_result(result: Any) -> str:
    """Short string summary of a tool result for the SSE tool_result event."""
    if isinstance(result, dict):
        if "error" in result:
            return f"error: {result['error']}"
        if "results" in result and isinstance(result["results"], list):
            return f"{len(result['results'])} results"
        if "checkpoints" in result and isinstance(result["checkpoints"], list):
            return f"{len(result['checkpoints'])} checkpoints"
        if "directives" in result and isinstance(result["directives"], list):
            return f"{len(result['directives'])} directives"
        if "call" in result and isinstance(result["call"], dict):
            return f"call {result['call'].get('id', '?')}"
    return "ok"


def _extract_citations(name: str, result: Any) -> list[dict[str, Any]]:
    """Pull citation rows from a tool result so the UI can render chips.

    Tool handlers that return `{"results": [{namespace, ref_id, text, score,
    metadata}]}` already match the citation shape — we forward those rows.
    `find_similar_failures` returns the same shape under "results".
    """
    out: list[dict[str, Any]] = []
    if not isinstance(result, dict):
        return out
    rows = result.get("results")
    if not isinstance(rows, list):
        return out
    for r in rows:
        if not isinstance(r, dict):
            continue
        # Best-effort coercion. Missing namespace is filled from the tool
        # name (search_scripts → scripts; find_similar_failures → transcripts).
        ns = r.get("namespace")
        if not ns:
            if name == "search_scripts":
                ns = "scripts"
            elif name == "find_similar_failures":
                ns = "transcripts"
            else:
                ns = "unknown"
        out.append({
            "namespace": ns,
            "ref_id": str(r.get("ref_id") or r.get("id") or ""),
            "text": r.get("text") or "",
            "score": float(r.get("score") or 0.0),
            "metadata": r.get("metadata") or {},
        })
    return out


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


async def _stream_chat(
    request: Request,
    payload: ChatRequest,
    db: Session,
) -> AsyncIterator[str]:
    """Yield SSE-framed events sourced from `agent_chat.run_chat`."""
    # Prepend the scope-aware system prompt (only if caller hasn't already
    # supplied one — respect the convention that the first system message wins).
    convo: list[dict[str, Any]] = []
    has_system = any(m.role == "system" for m in payload.messages)
    if not has_system:
        convo.append({"role": "system", "content": _build_system_prompt(payload.call_id)})
    for m in payload.messages:
        convo.append({"role": m.role, "content": m.content})

    yield ": connected\n\n"

    try:
        async for event_type, ev_payload in agent_chat.run_chat(convo, db):
            # Bail if the client hung up — saves backend tokens on aborts.
            if await request.is_disconnected():
                logger.info("AGENT_CHAT client disconnected, closing stream")
                return

            if event_type == "token":
                yield _sse("token", {"delta": ev_payload})

            elif event_type == "tool_call":
                # ev_payload is {"name": str, "arguments": dict}. We don't
                # have the result yet — `run_chat` dispatches it inline and
                # appends the tool message to its own conversation. We can
                # still surface the call to the UI immediately.
                yield _sse("tool_call", {
                    "name": ev_payload.get("name"),
                    "input": ev_payload.get("arguments") or {},
                })
                # Re-dispatch the tool here ONLY to capture the result for
                # citation extraction. `run_chat` already executed it (and
                # advanced the convo), so we accept the small duplication —
                # tools are idempotent reads.
                try:
                    from app.agent import rag_tools

                    result = rag_tools.dispatch(
                        db,
                        ev_payload.get("name", ""),
                        ev_payload.get("arguments") or {},
                    )
                    yield _sse("tool_result", {
                        "name": ev_payload.get("name"),
                        "summary": _summarize_result(result),
                        "ok": "error" not in (result if isinstance(result, dict) else {}),
                    })
                    for cite in _extract_citations(ev_payload.get("name", ""), result):
                        yield _sse("citation", cite)
                except Exception as e:  # noqa: BLE001
                    logger.warning("AGENT_CHAT cite-extract failed: %s", e)

            elif event_type == "done":
                usage = {
                    "finish_reason": (ev_payload or {}).get("finish_reason"),
                    "iterations": (ev_payload or {}).get("iterations"),
                }
                yield _sse("end", usage)
                return

    except EnvironmentError as e:
        # Missing OPENROUTER_API_KEY — surface a clean end event so the UI
        # can render "chat unavailable" instead of hanging.
        logger.warning("AGENT_CHAT env error: %s", e)
        yield _sse("end", {"finish_reason": "unavailable", "error": str(e)})
    except Exception as e:  # noqa: BLE001
        logger.exception("AGENT_CHAT stream error")
        yield _sse("end", {"finish_reason": "error", "error": str(e)})


@agent_chat_router.post("/chat")
async def post_chat(
    payload: ChatRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> StreamingResponse:
    return StreamingResponse(
        _stream_chat(request, payload, db),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
