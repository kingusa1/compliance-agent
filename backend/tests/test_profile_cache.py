"""Tests for the module-level profile cache (app.profile_cache).

Covers:
- Empty cache returns empty dict on first call (mocked DB).
- Second call returns cached data without re-querying the DB.
- TTL expiry triggers a fresh DB query.
- invalidate_profile_cache() forces a refresh on next call.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_mock_db(profiles: list[dict]) -> MagicMock:
    """Return a SQLAlchemy Session mock whose query(...).all() yields fake Profile rows."""
    rows = []
    for p in profiles:
        row = MagicMock()
        row.id = p["id"]
        row.name = p["name"]
        row.email = p.get("email", f"{p['id']}@test.com")
        row.role = p.get("role", "reviewer")
        row.active = p.get("active", True)
        rows.append(row)

    db = MagicMock()
    db.query.return_value.all.return_value = rows
    return db


def _reset_cache():
    """Reset global cache state between tests."""
    import app.profile_cache as pc
    pc._PROFILE_CACHE = {}
    pc._loaded_at = None


# ── tests ─────────────────────────────────────────────────────────────────────

def test_empty_db_returns_empty_dict():
    """When the DB has no profiles, get_profile_dict returns {}."""
    _reset_cache()
    from app.profile_cache import get_profile_dict

    db = _make_mock_db([])
    result = get_profile_dict(db)
    assert result == {}


def test_first_call_populates_cache():
    """After the first get_profile_dict call, the cache is populated."""
    _reset_cache()
    from app import profile_cache as pc
    from app.profile_cache import get_profile_dict

    db = _make_mock_db([{"id": "u1", "name": "Alice"}])
    result = get_profile_dict(db)

    assert "u1" in result
    assert result["u1"]["name"] == "Alice"
    assert result["u1"]["email"] == "u1@test.com"
    assert result["u1"]["role"] == "reviewer"
    assert result["u1"]["is_active"] is True
    assert pc._loaded_at is not None


def test_second_call_does_not_re_query_db():
    """Second call within TTL returns cached data without hitting the DB again."""
    _reset_cache()
    from app.profile_cache import get_profile_dict

    db = _make_mock_db([{"id": "u1", "name": "Alice"}])

    get_profile_dict(db)  # first — populates cache
    db.query.reset_mock()

    get_profile_dict(db)  # second — should use cache
    db.query.assert_not_called()


def test_ttl_expiry_triggers_refresh():
    """When _loaded_at is older than TTL, a fresh DB query is issued."""
    _reset_cache()
    import app.profile_cache as pc
    from app.profile_cache import get_profile_dict

    db = _make_mock_db([{"id": "u2", "name": "Bob"}])

    # Pre-populate cache with a stale timestamp.
    pc._PROFILE_CACHE = {"u2": {"id": "u2", "name": "OldBob", "email": "u2@test.com", "role": "reviewer", "is_active": True}}
    pc._loaded_at = datetime.now(tz=timezone.utc) - timedelta(minutes=6)  # older than 5-min TTL

    result = get_profile_dict(db)

    # DB was re-queried — name reflects the mock's fresh data.
    db.query.assert_called()
    assert result["u2"]["name"] == "Bob"


def test_invalidate_forces_refresh_on_next_call():
    """invalidate_profile_cache() clears state so the next call re-queries."""
    _reset_cache()
    import app.profile_cache as pc
    from app.profile_cache import get_profile_dict, invalidate_profile_cache

    db = _make_mock_db([{"id": "u3", "name": "Carol"}])

    # Fill cache.
    pc._PROFILE_CACHE = {"u3": {"id": "u3", "name": "OldCarol", "email": "u3@test.com", "role": "reviewer", "is_active": True}}
    pc._loaded_at = datetime.now(tz=timezone.utc)

    invalidate_profile_cache()
    assert pc._loaded_at is None
    assert pc._PROFILE_CACHE == {}

    # Next call must re-query.
    result = get_profile_dict(db)
    db.query.assert_called()
    assert result["u3"]["name"] == "Carol"


def test_get_profile_names_returns_string_map():
    """get_profile_names returns {id: name} string-to-string dict."""
    _reset_cache()
    from app.profile_cache import get_profile_names

    db = _make_mock_db([
        {"id": "u1", "name": "Alice"},
        {"id": "u2", "name": "Bob"},
    ])
    names = get_profile_names(db)

    assert names == {"u1": "Alice", "u2": "Bob"}


def test_refresh_profile_cache_returns_count():
    """refresh_profile_cache returns the number of profiles loaded."""
    _reset_cache()
    from app.profile_cache import refresh_profile_cache

    db = _make_mock_db([
        {"id": "u1", "name": "Alice"},
        {"id": "u2", "name": "Bob"},
        {"id": "u3", "name": "Carol"},
    ])
    count = refresh_profile_cache(db)

    assert count == 3
