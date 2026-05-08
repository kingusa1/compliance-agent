"""Fire-and-forget Inngest emit helpers for tracker observability.

Routes call ``emit_event(event_name, data)`` and continue without awaiting
the network round-trip. The Inngest dashboard then shows every state
change in the pipeline (uploads, verdicts, rejections, deal-status flips,
metadata edits, tracker queries, XLSX exports, portal-batch submissions).

Failures are swallowed + logged — observability must never break the
business path.
"""
from __future__ import annotations
import asyncio
from typing import Any, Mapping

import inngest as _inngest

from app.inngest_client import inngest_client
from app.logger import log


import os as _os

# Kill-switch: when DISABLE_INNGEST_EMIT=1, every emit_event becomes a
# no-op. Used on the VPS demo where there's no Inngest dev server and
# the polling registration storm pegs CPU at 250%+.
_EMIT_DISABLED = _os.getenv("DISABLE_INNGEST_EMIT", "").lower() in ("1", "true", "yes")


def emit_event(name: str, data: Mapping[str, Any]) -> None:
    """Fire-and-forget event emit. Safe to call from sync code paths.

    Schedules the async ``inngest_client.send`` on a background task if
    an event loop is running, else runs to completion synchronously.
    Exceptions are caught + logged, never raised.
    """
    if _EMIT_DISABLED:
        return
    payload = {"name": name, "data": dict(data)}

    async def _send() -> None:
        try:
            await inngest_client.send(_inngest.Event(name=name, data=dict(data)))
        except Exception as exc:  # pragma: no cover — observability must not break business path
            log.warning(f"INNGEST_EMIT_FAILED name={name} err={type(exc).__name__}: {exc}")

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_send())
    except RuntimeError:
        # No event loop — sync caller. Run synchronously and swallow errors.
        try:
            asyncio.run(_send())
        except Exception as exc:  # pragma: no cover
            log.warning(f"INNGEST_EMIT_FAILED name={name} err={type(exc).__name__}: {exc}")


async def emit_event_async(name: str, data: Mapping[str, Any]) -> None:
    """Async variant — awaits the send. Use from async route handlers
    where you want to be sure the event left the process before the
    response is returned. Still swallows failures."""
    if _EMIT_DISABLED:
        return
    try:
        await inngest_client.send(_inngest.Event(name=name, data=dict(data)))
    except Exception as exc:
        log.warning(f"INNGEST_EMIT_FAILED name={name} err={type(exc).__name__}: {exc}")
