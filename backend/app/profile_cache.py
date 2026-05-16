"""Module-level profile cache with 5-minute TTL.

Profiles (reviewers) are a small, rarely-changing table. Querying all rows on
every queue render is wasted DB work. This module provides a process-level
in-memory cache that is pre-loaded at FastAPI startup and auto-refreshed on a
5-minute TTL.

Public API
----------
get_profile_dict(db, force=False) -> dict[str, dict]
    Returns {profile_id: {"id", "name", "email", "role", "is_active"}}.
    Refreshes from DB when stale or force=True.

get_profile_names(db, force=False) -> dict[str, str]
    Convenience wrapper: returns {profile_id: name} for call sites that only
    need the name string.

invalidate_profile_cache() -> None
    Clears the in-memory cache so the next call forces a DB round-trip.
    Used by Realtime subscription handlers when a profile row changes.

refresh_profile_cache(db) -> int
    Explicit refresh; returns the number of profiles loaded.
    Called at FastAPI startup and via the manual-refresh endpoint.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

# ── Cache state ──────────────────────────────────────────────────────────────

_PROFILE_CACHE: dict[str, dict] = {}
_loaded_at: Optional[datetime] = None
_lock = threading.Lock()  # synchronous lock — cache is accessed from sync routes

_TTL = timedelta(minutes=5)


# ── Internal helpers ─────────────────────────────────────────────────────────

def _is_stale() -> bool:
    if _loaded_at is None:
        return True
    return (datetime.now(tz=timezone.utc) - _loaded_at) > _TTL


def _load_from_db(db: Session) -> dict[str, dict]:
    """Query all Profile rows and return a fresh slim dict."""
    from app.models import Profile  # late import to avoid circular deps at module load

    return {
        p.id: {
            "id": p.id,
            "name": p.name,
            "email": p.email,
            "role": p.role,
            "is_active": getattr(p, "active", True),
        }
        for p in db.query(Profile).all()
    }


# ── Public API ───────────────────────────────────────────────────────────────

def get_profile_dict(db: Session, force: bool = False) -> dict[str, dict]:
    """Return the cached profile dict, refreshing if stale or ``force=True``.

    Thread-safe via module-level ``threading.Lock``. Callers on the hot path
    (queue render, summary render) should NOT pass ``force=True`` — let the TTL
    govern refresh timing.
    """
    global _PROFILE_CACHE, _loaded_at

    with _lock:
        if force or _is_stale():
            _PROFILE_CACHE = _load_from_db(db)
            _loaded_at = datetime.now(tz=timezone.utc)
            log.debug("profile_cache: refreshed %d profiles", len(_PROFILE_CACHE))
        return dict(_PROFILE_CACHE)  # shallow copy — callers must not mutate


def get_profile_names(db: Session, force: bool = False) -> dict[str, str]:
    """Convenience wrapper returning ``{profile_id: name}`` (string→string).

    Drop-in replacement for the old one-liner::

        name_map = {p.id: p.name for p in db.query(Profile).all()}
    """
    return {pid: info["name"] for pid, info in get_profile_dict(db, force=force).items()}


def invalidate_profile_cache() -> None:
    """Clear the cache so the next ``get_profile_dict`` call forces a DB round-trip."""
    global _PROFILE_CACHE, _loaded_at

    with _lock:
        _PROFILE_CACHE = {}
        _loaded_at = None
    log.debug("profile_cache: invalidated")


def refresh_profile_cache(db: Session) -> int:
    """Explicitly refresh the cache from DB and return the number of profiles loaded.

    Intended for startup pre-load and the manual ``POST /api/internal/refresh-profile-cache``
    endpoint. Always performs a DB query regardless of TTL.
    """
    profiles = get_profile_dict(db, force=True)
    return len(profiles)
