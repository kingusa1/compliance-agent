"""In-memory pub/sub for live call/pipeline events.

Wires the legacy `process_call` pipeline (app.pipeline._trace_step) and the
upload boundary (app.routes.upload_call) to any SSE subscriber currently
listening on /api/calls/events or /api/calls/{call_id}/events. Replaces the
2026-05-16 aggressive `refetchInterval` polling (reverted in `e1c8d3b`) with
a true push: the frontend invalidates the relevant React Query key the moment
the backend emits an event.

Design choices:

* Single-process in-memory broadcast — Railway runs one uvicorn worker, so a
  Redis pub/sub would add ops weight for no functional gain. If we ever
  scale to >1 worker, swap _SUBSCRIBERS for a redis.asyncio.Redis() PSUBSCRIBE.
* Subscribers get an unbounded asyncio.Queue; publish() never blocks. If a
  subscriber stalls, we drop them after 1000 queued events rather than
  back-pressure the publisher.
* Two subscription scopes: ``call_id="*"`` (global, for queue/tracker pages)
  and ``call_id="<uuid>"`` (per-call, for call detail). publish() fans out
  to both: every event also lands on the global queue, so a single subscriber
  on "*" can drive any list view.
* Event shape: {"event_type": str, "call_id": str, "ts": iso8601, "payload": dict}.
  event_type values: ``queued``, ``transcribe_done``, ``detect_metadata_done``,
  ``segments_detected``, ``checkpoints_scored``, ``score_ready``,
  ``finalized``, ``failed``, ``step_started``, ``step_ok``, ``step_err``.

Survives the process — no persistence. Replays after server restart rely on
the client re-fetching the call detail; EventSource auto-reconnects and we
emit a fresh "hello" sentinel on each open, so the client can mark the
connection healthy without missing the next live transition.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime
from typing import Any, AsyncIterator

log = logging.getLogger(__name__)

# Map "<call_id>" or "*" → list of asyncio.Queue instances (one per subscriber).
_SUBSCRIBERS: dict[str, list[asyncio.Queue]] = defaultdict(list)
_MAX_QUEUED = 1000
_GLOBAL = "*"


def _now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="milliseconds") + "Z"


def publish(call_id: str, event_type: str, payload: dict[str, Any] | None = None) -> None:
    """Fan-out a single event to every subscriber on this call_id AND on the
    global "*" scope. Safe to call from sync code — uses Queue.put_nowait so
    no event-loop hop required.
    """
    if not call_id or not event_type:
        return
    event = {
        "event_type": event_type,
        "call_id": str(call_id),
        "ts": _now_iso(),
        "payload": payload or {},
    }
    for scope in (call_id, _GLOBAL):
        queues = list(_SUBSCRIBERS.get(scope, []))
        for q in queues:
            if q.qsize() > _MAX_QUEUED:
                # Subscriber wedged — drop them rather than back-pressure the
                # publisher. They'll re-attach on the next EventSource reconnect.
                try:
                    _SUBSCRIBERS[scope].remove(q)
                except ValueError:
                    pass
                continue
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Bounded queues only — we use unbounded, so this branch is
                # defensive. Drop silently if we ever switch to bounded.
                pass


async def subscribe(call_id: str) -> AsyncIterator[dict[str, Any]]:
    """Async generator yielding events for one scope. Call with ``"*"`` for
    the global feed (every event from every call) or ``"<call_id>"`` for a
    single call. Caller is responsible for cancelling — the generator will
    self-clean on cancellation so subscriber list doesn't leak.
    """
    queue: asyncio.Queue = asyncio.Queue()
    _SUBSCRIBERS[call_id].append(queue)
    log.info(
        f"REALTIME subscribed scope={call_id} (total_subs={len(_SUBSCRIBERS[call_id])})"
    )
    try:
        while True:
            event = await queue.get()
            yield event
    finally:
        try:
            _SUBSCRIBERS[call_id].remove(queue)
        except ValueError:
            pass
        if not _SUBSCRIBERS[call_id] and call_id != _GLOBAL:
            # Clean up empty per-call entries; keep "*" forever.
            _SUBSCRIBERS.pop(call_id, None)
        log.info(
            f"REALTIME unsubscribed scope={call_id} "
            f"(remaining_subs={len(_SUBSCRIBERS.get(call_id, []))})"
        )


def subscriber_count(call_id: str | None = None) -> int:
    """Diagnostic: how many subscribers on a given scope (or total)."""
    if call_id is None:
        return sum(len(v) for v in _SUBSCRIBERS.values())
    return len(_SUBSCRIBERS.get(call_id, []))
