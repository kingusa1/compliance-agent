"""SSE endpoints that expose app.realtime to the frontend.

Two endpoints, both returning ``text/event-stream``:

* ``GET /api/calls/events`` — global feed, every step transition from every
  in-flight call. Used by list pages (queue, tracker, all-calls) to invalidate
  their list query keys the moment a new call lands or an existing call
  changes status.
* ``GET /api/calls/{call_id}/events`` — per-call feed. Used by the call
  detail page so it can refresh the call payload as each pipeline step
  finishes, without polling.

Both emit:
* ``: connected`` comment immediately after the connection opens (clients
  can use this as a health signal).
* ``: keep-alive`` comment every 5s so proxies / browsers don't time the
  connection out.
* ``event: <event_type>`` + ``data: <json>`` blocks for every published
  realtime event.

EventSource cannot inject Authorization headers, so these endpoints are
intentionally unauthenticated (matches the existing pattern in
app.observability_routes.stream_runs). They only emit event_type + call_id
+ step name — no transcript / PII content — so the leak surface is bounded
to "this call exists and reached step X".
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app import realtime

log = logging.getLogger(__name__)

realtime_router = APIRouter(tags=["realtime"])

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    # nginx / Cloudflare buffer text/event-stream by default; this disables it.
    "X-Accel-Buffering": "no",
}


async def _stream_events(request: Request, scope: str):
    """Shared SSE generator for both endpoints. Yields :connected, then a
    :keep-alive every 5s, then each event from the subscription queue.
    Closes cleanly on client disconnect.
    """
    yield ": connected\n\n"
    last_keepalive = time.time()
    sub = realtime.subscribe(scope).__aiter__()
    pending: asyncio.Task | None = asyncio.create_task(sub.__anext__())
    try:
        while True:
            if await request.is_disconnected():
                log.info(f"REALTIME client disconnected scope={scope}")
                return
            now = time.time()
            # Heartbeat every 5s so proxies / browsers keep the conn alive.
            wait_for = max(0.1, 5.0 - (now - last_keepalive))
            try:
                event = await asyncio.wait_for(asyncio.shield(pending), timeout=wait_for)
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"
                last_keepalive = time.time()
                continue
            # Got a real event — emit it, then re-arm the pending task.
            yield f"event: {event['event_type']}\ndata: {json.dumps(event)}\n\n"
            last_keepalive = time.time()
            pending = asyncio.create_task(sub.__anext__())
    except asyncio.CancelledError:
        raise
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
        try:
            await sub.aclose()  # type: ignore[attr-defined]
        except Exception:
            pass


@realtime_router.get("/api/calls/events")
async def stream_global_events(request: Request) -> StreamingResponse:
    """Global feed — every call event."""
    return StreamingResponse(
        _stream_events(request, "*"),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@realtime_router.get("/api/calls/{call_id}/events")
async def stream_call_events(call_id: str, request: Request) -> StreamingResponse:
    """Per-call feed — events for a single call_id only."""
    return StreamingResponse(
        _stream_events(request, call_id),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
