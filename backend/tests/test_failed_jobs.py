"""Test failed_jobs table + writer.

Coverage:
  - migration creates table with expected columns + indexes
  - FailedJob ORM round-trips
  - record_failed_job() writes one row idempotently per (call_id, attempts)
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import inspect

from app.database import SessionLocal, engine


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def test_failed_jobs_table_exists():
    insp = inspect(engine)
    assert "failed_jobs" in insp.get_table_names()
    cols = {c["name"] for c in insp.get_columns("failed_jobs")}
    assert {
        "id", "call_id", "last_step", "attempts", "last_error",
        "exhausted_at", "created_at",
    }.issubset(cols)


def test_failed_jobs_writer(db):
    from app.models import Call, FailedJob
    from app.workflows.redispatch_watchdog import record_failed_job

    call = Call(
        id=str(uuid.uuid4()),
        filename="t.mp3",
        file_path="/tmp/t.mp3",
        status="failed",
    )
    db.add(call)
    db.commit()

    record_failed_job(
        db,
        call_id=call.id,
        last_step="analyze_checkpoints",
        attempts=3,
        last_error="OpenAI 429",
    )
    db.commit()

    rows = db.query(FailedJob).filter_by(call_id=call.id).all()
    assert len(rows) == 1
    assert rows[0].last_step == "analyze_checkpoints"
    assert rows[0].attempts == 3


def test_failed_jobs_writer_is_idempotent(db):
    from app.models import Call, FailedJob
    from app.workflows.redispatch_watchdog import record_failed_job

    call = Call(
        id=str(uuid.uuid4()),
        filename="t.mp3",
        file_path="/tmp/t.mp3",
        status="failed",
    )
    db.add(call)
    db.commit()

    record_failed_job(db, call_id=call.id, last_step="x", attempts=3, last_error="a")
    db.commit()
    record_failed_job(db, call_id=call.id, last_step="x", attempts=3, last_error="b")
    db.commit()

    assert db.query(FailedJob).filter_by(call_id=call.id).count() == 1


def test_handle_exhausted_run_writes_failed_jobs_row(db):
    """When _handle_exhausted_run is invoked, it writes one failed_jobs row
    and flips the Call.status to failed."""
    from app.models import Call, FailedJob
    from app.workflows.redispatch_watchdog import _handle_exhausted_run

    call = Call(id=str(uuid.uuid4()), filename="t.mp3", file_path="/tmp/t.mp3",
                status="processing",
                last_step_name="analyze_checkpoints", last_step_error="boom")
    db.add(call); db.commit()

    _handle_exhausted_run(db, call_id=call.id, attempts=3)
    db.commit()

    rows = db.query(FailedJob).filter_by(call_id=call.id).all()
    assert len(rows) == 1
    assert rows[0].last_error == "boom"
    db.refresh(call)
    assert call.status == "failed"
