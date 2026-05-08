"""Postgres LISTEN/NOTIFY event emission (Task 30).

Decouples side effects (learning extraction, analytics, Slack notifications)
from the HITL request path. Endpoints call `emit()` after committing; a
background listener (started in lifespan) dispatches to handlers.

On SQLite (tests) emit() is a silent no-op — LISTEN/NOTIFY is Postgres-only.
"""

import json
import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

CHANNEL = "hitl_events"


def emit(db: Session, event_type: str, payload: dict) -> None:
    try:
        body = json.dumps({"type": event_type, **payload})
        db.execute(text(f"SELECT pg_notify(:channel, :payload)"), {
            "channel": CHANNEL,
            "payload": body,
        })
        logger.debug("event emitted type=%s", event_type)
    except Exception:
        # SQLite or disconnected — silently skip. The inline fallback
        # (abstract_and_store_review) still runs in the same request.
        pass
