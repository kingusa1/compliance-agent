"""Tests for HITL (Human-in-the-Loop) models and Call table additions.

Adapted from the original plan to use the project's existing ``test_db``
fixture (SQLite, from ``conftest.py``) instead of hitting the live Supabase
Postgres engine imported from ``app.database``. Running the plan's original
fixture (``Base.metadata.drop_all(engine)``) against the live pooler URL
would destroy production tables.
"""

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import (
    Call,
    ClaimLock,
    ComplianceDecision,
    ReviewSession,
    TranscriptEdit,
    VerdictHistory,
)


def test_call_has_compliance_columns(test_db):
    c = Call(id="c1", filename="x.mp3", file_path="/uploads/x.mp3", transcript="...", duration_seconds=10)
    test_db.add(c)
    test_db.commit()
    fetched = test_db.query(Call).one()
    assert fetched.compliance_status == "pending"
    assert fetched.compliance_source is None
    assert fetched.compliance_comment is None
    assert fetched.review_status == "unclaimed"


def test_review_session_round_trip(test_db):
    test_db.add(Call(id="c1", filename="x.mp3", file_path="/uploads/x.mp3"))
    rs = ReviewSession(id="rs1", call_id="c1", reviewer_id="sarah", is_active=True)
    test_db.add(rs)
    test_db.commit()
    assert test_db.query(ReviewSession).one().reviewer_id == "sarah"


def test_verdict_history_allows_multiple_rows_same_checkpoint(test_db):
    test_db.add(Call(id="c1", filename="x.mp3", file_path="/uploads/x.mp3"))
    test_db.add(
        VerdictHistory(
            id="v1",
            call_id="c1",
            checkpoint_id="cp_1",
            actor_type="ai",
            actor_id="agent",
            verdict="pass",
            reasoning="ok",
            is_current=False,
        )
    )
    test_db.add(
        VerdictHistory(
            id="v2",
            call_id="c1",
            checkpoint_id="cp_1",
            actor_type="reviewer",
            actor_id="sarah",
            verdict="fail",
            reasoning="missed DD",
            is_current=True,
        )
    )
    test_db.commit()
    assert test_db.query(VerdictHistory).filter_by(checkpoint_id="cp_1").count() == 2
    assert (
        test_db.query(VerdictHistory)
        .filter_by(checkpoint_id="cp_1", is_current=True)
        .one()
        .verdict
        == "fail"
    )


def test_transcript_edit_records_old_and_new(test_db):
    test_db.add(Call(id="c1", filename="x.mp3", file_path="/uploads/x.mp3"))
    test_db.add(ReviewSession(id="rs1", call_id="c1", reviewer_id="sarah"))
    te = TranscriptEdit(
        id="te1",
        call_id="c1",
        word_index=42,
        word_start_ms=12300,
        old_text="yeah",
        new_text="yes",
        edited_by="sarah",
        review_session_id="rs1",
    )
    test_db.add(te)
    test_db.commit()
    assert test_db.query(TranscriptEdit).one().new_text == "yes"


def test_claim_lock_pk_on_call_id(test_db):
    from datetime import datetime, timedelta

    test_db.add(Call(id="c1", filename="x.mp3", file_path="/uploads/x.mp3"))
    test_db.add(ReviewSession(id="rs1", call_id="c1", reviewer_id="sarah"))
    test_db.add(ReviewSession(id="rs2", call_id="c1", reviewer_id="mo"))
    test_db.commit()
    expires_at = datetime.utcnow() + timedelta(minutes=30)
    test_db.add(
        ClaimLock(
            call_id="c1",
            reviewer_id="sarah",
            review_session_id="rs1",
            expires_at=expires_at,
        )
    )
    test_db.commit()
    with pytest.raises(IntegrityError):
        test_db.add(
            ClaimLock(
                call_id="c1",
                reviewer_id="mo",
                review_session_id="rs2",
                expires_at=expires_at,
            )
        )
        test_db.commit()


def test_compliance_decision_history(test_db):
    test_db.add(Call(id="c1", filename="x.mp3", file_path="/uploads/x.mp3"))
    test_db.add(
        ComplianceDecision(
            id="cd1",
            call_id="c1",
            status="non_compliant",
            actor_type="system",
            actor_id="system",
            is_current=False,
        )
    )
    test_db.add(
        ComplianceDecision(
            id="cd2",
            call_id="c1",
            status="compliant",
            actor_type="lead",
            actor_id="omar",
            comment="Cleared",
            is_current=True,
        )
    )
    test_db.commit()
    assert test_db.query(ComplianceDecision).filter_by(call_id="c1").count() == 2
    cur = (
        test_db.query(ComplianceDecision)
        .filter_by(call_id="c1", is_current=True)
        .one()
    )
    assert cur.actor_id == "omar"
