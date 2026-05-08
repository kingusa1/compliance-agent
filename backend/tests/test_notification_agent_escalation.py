"""Tests for app.notifications.agent_escalation — pure compute, no
external services. Uses an in-memory SQLite DB to exercise the
threshold logic. Distinct from tests/test_agent_escalation.py which
covers the smart-agent LLM-tier escalation, not management escalation.
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.notifications.agent_escalation import find_agents_for_escalation


@pytest.fixture
def db():
    """Disposable SQLite session with full schema from app.models."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
    import app.models  # noqa: F401  — registers tables on Base.metadata
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    sess = Session()
    yield sess
    sess.close()
    engine.dispose()
    try:
        os.unlink(path)
    except PermissionError:
        # Windows-only: file handle still draining. Harmless.
        pass


def _make_rejection(db, *, sales_agent: str | None,
                    category: str = "COMPLIANCE_ISSUE",
                    rejection_reason: str = "Identity not stated",
                    rejected_at: datetime | None = None):
    from app.models import Rejection
    r = Rejection(
        sales_agent=sales_agent,
        category=category,
        rejection_reason=rejection_reason,
        rejected_at=rejected_at or datetime.now(timezone.utc),
        status="NOT_STARTED",
    )
    db.add(r)
    db.flush()
    return r


def test_no_rejections_returns_empty(db):
    assert find_agents_for_escalation(db) == []


def test_below_threshold_not_escalated(db):
    for _ in range(2):
        _make_rejection(db, sales_agent="alice")
    db.commit()
    assert find_agents_for_escalation(db, threshold=3) == []


def test_at_threshold_is_escalated(db):
    for _ in range(3):
        _make_rejection(db, sales_agent="alice")
    db.commit()
    out = find_agents_for_escalation(db, threshold=3)
    assert len(out) == 1
    assert out[0].sales_agent == "alice"
    assert out[0].critical_count == 3


def test_only_compliance_issue_counts_as_critical(db):
    for _ in range(3):
        _make_rejection(db, sales_agent="bob", category="ADMIN_ERROR",
                        rejection_reason="Wrong name on LOA")
    db.commit()
    assert find_agents_for_escalation(db, threshold=3) == []


def test_critical_keyword_in_text_counts(db):
    for _ in range(3):
        _make_rejection(db, sales_agent="bob", category="VERBAL_SALES_ERROR",
                        rejection_reason="Vulnerable customer not handled correctly")
    db.commit()
    out = find_agents_for_escalation(db, threshold=3)
    assert len(out) == 1
    assert out[0].sales_agent == "bob"


def test_window_excludes_old_rejections(db):
    long_ago = datetime.now(timezone.utc) - timedelta(days=30)
    for _ in range(5):
        _make_rejection(db, sales_agent="alice", rejected_at=long_ago)
    db.commit()
    assert find_agents_for_escalation(db, window_days=7) == []


def test_multiple_agents_sorted_by_count_desc(db):
    for _ in range(3):
        _make_rejection(db, sales_agent="alice")
    for _ in range(5):
        _make_rejection(db, sales_agent="bob")
    db.commit()
    out = find_agents_for_escalation(db)
    assert [e.sales_agent for e in out] == ["bob", "alice"]
    assert [e.critical_count for e in out] == [5, 3]


def test_rejection_code_extracted_from_text(db):
    _make_rejection(db, sales_agent="alice",
                    rejection_reason="R01 — identity failure")
    _make_rejection(db, sales_agent="alice",
                    rejection_reason="Some other R09 issue")
    _make_rejection(db, sales_agent="alice",
                    rejection_reason="No code in this one")
    db.commit()
    out = find_agents_for_escalation(db, threshold=3)
    assert set(out[0].rejection_codes) >= {"R01", "R09"}
    assert "?" in out[0].rejection_codes


def test_null_sales_agent_excluded(db):
    for _ in range(5):
        _make_rejection(db, sales_agent=None)
    db.commit()
    assert find_agents_for_escalation(db) == []
