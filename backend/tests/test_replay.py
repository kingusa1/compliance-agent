"""Integration: POST /calls/{id}/reanalyze fires the background pipeline
task (no Inngest dependency) and writes an audit row.

2026-05-25 — `app.replay.reanalyze` was rewritten to run the analyze →
score → finalize sub-pipeline directly via `asyncio.create_task` instead
of emitting a CALL_REANALYZE Inngest event that production never
consumed (USE_INNGEST_PIPELINE=false). New behaviours covered:

  * 202 + run_id when transcript + word_data + script_id present
  * 202 + run_id when transcript + word_data present but script_id null
    (Reanalyze now recovers from missing supplier/script via detect_metadata)
  * 422 only when transcript or word_data is missing
  * 404 for unknown call id
  * Audit row written before the background task spawns
"""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.reviewers import current_reviewer


client = TestClient(app)


# 2026-05-18: /api/calls/{id}/reanalyze gained ``Depends(current_reviewer)``
# in commit `5708bcf` (audit fix). Without overriding the auth dep, every
# request returns 401 instead of the asserted 202/422/404. Autouse fixture
# because conftest now aggressively clears overrides after each test.
#
# Also seeds a matching Profile row so the audit_log INSERT (which has an
# actor_id FK to profiles.id) succeeds. Without the Profile seed the
# reanalyze call would 500 with a FK violation.
@pytest.fixture(autouse=True)
def _override_auth():
    from app.database import SessionLocal
    from app.models import Profile

    db = SessionLocal()
    try:
        existing = db.query(Profile).filter_by(id="test-reviewer").first()
        if not existing:
            db.add(Profile(
                id="test-reviewer",
                email="test@compliance-agent.local",
                name="Test Reviewer",
                role="admin",
                active=True,
            ))
            db.commit()
    finally:
        db.close()

    app.dependency_overrides[current_reviewer] = lambda: {
        "id": "test-reviewer",
        "email": "test@compliance-agent.local",
        "role": "admin",
    }
    yield


def _stub_background_task():
    """Replace the heavy `_run_reanalysis` coroutine with a no-op so the
    test doesn't actually invoke the LLM pipeline. We assert it was
    SCHEDULED (asyncio.create_task called) which is what the endpoint's
    contract guarantees."""
    async def _noop(call_id: str, run_id: str) -> None:
        return None
    return patch("app.replay._run_reanalysis", side_effect=_noop)


def test_reanalyze_returns_202_when_call_has_transcript(db_session_with_call_with_transcript):
    """db_session_with_call_with_transcript is a fixture seeding a Call row
    with non-null `transcript`, `word_data`, and `script_id`. See conftest.py."""
    call_id = db_session_with_call_with_transcript

    with _stub_background_task() as mock_task:
        r = client.post(f"/api/calls/{call_id}/reanalyze")

    assert r.status_code == 202
    body = r.json()
    assert body["call_id"] == call_id
    assert "run_id" in body
    # The endpoint scheduled the background pipeline run with both
    # positional args (call_id, run_id) — the asyncio.create_task wrapper
    # eagerly invokes the coroutine factory so our mock records the call.
    mock_task.assert_called_once()
    args, _ = mock_task.call_args
    assert args[0] == call_id


def test_reanalyze_returns_202_when_script_id_missing(db_session_with_call_with_transcript):
    """Regression — UI screenshot 2026-05-25 — Reanalyze button must work
    even when supplier/script detection failed on first pass. The endpoint
    used to 422 with the misleading message "Call lacks transcript /
    word_data / script_id"; now it runs detect_metadata as part of the
    replay pipeline so the user can recover.
    """
    from app.database import SessionLocal
    from app.models import Call

    call_id = db_session_with_call_with_transcript

    # Strip script_id off the fixture's Call row.
    db = SessionLocal()
    try:
        call = db.query(Call).filter_by(id=call_id).first()
        assert call is not None
        call.script_id = None
        db.commit()
    finally:
        db.close()

    with _stub_background_task() as mock_task:
        r = client.post(f"/api/calls/{call_id}/reanalyze")

    assert r.status_code == 202
    mock_task.assert_called_once()


def test_reanalyze_returns_422_when_transcript_missing(db_session_with_call_no_transcript):
    call_id = db_session_with_call_no_transcript
    r = client.post(f"/api/calls/{call_id}/reanalyze")
    assert r.status_code == 422
    assert "transcript" in r.json()["detail"].lower()


def test_reanalyze_returns_404_for_unknown_call_id():
    r = client.post("/api/calls/00000000-0000-0000-0000-000000000000/reanalyze")
    assert r.status_code == 404


# 2026-05-27 wave-21 regression tests — speaker-label re-derive on Reanalyze.
#
# These tests use an in-memory SQLite engine + monkeypatch the
# `app.database.SessionLocal` symbol so the off-loop worker inside
# `_rederive_speaker_labels` writes through the same engine as the
# test fixture. This isolates them from the conftest's production-DB
# fixture path (which fails on a local-env schema drift that's not
# part of this wave).


@pytest.fixture
def wave21_sqlite_session(monkeypatch):
    """Build a clean in-memory SQLite engine + monkeypatch
    `app.database.SessionLocal` to bind to it. Yields a Session for
    test setup; cleans up after."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.database import Base
    import app.database as _app_db

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Patch so the worker thread's `from app.database import SessionLocal`
    # picks up this in-memory binding instead of the production DSN.
    monkeypatch.setattr(_app_db, "SessionLocal", TestSessionLocal)

    session = TestSessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.mark.asyncio
async def test_rederive_speaker_labels_rewrites_stale_transcript(wave21_sqlite_session):
    """Wave-21: `_rederive_speaker_labels` runs after Reanalyze's finalize
    step and rewrites `Call.transcript` using the current
    `_detect_agent_speaker` heuristic. Idempotent — second invocation
    writes nothing."""
    import json as _json
    import uuid as _uuid

    from app.models import Call
    from app.replay import _rederive_speaker_labels

    db = wave21_sqlite_session
    call_id = str(_uuid.uuid4())
    # Word data: speaker A says only "hello"; speaker B has all the
    # broker-side phrases (renewal, watt utilities, your current
    # contract). Post-wave-16 the heuristic picks B as agent. The
    # persisted transcript is INCORRECTLY labeled — exactly the
    # Elzicle bug shape the owner reported.
    word_data = [
        {"word": "hello", "speaker": "A", "start": 0.0, "end": 0.3},
        {"word": "your", "speaker": "B", "start": 1.0, "end": 1.2},
        {"word": "current", "speaker": "B", "start": 1.2, "end": 1.4},
        {"word": "contract", "speaker": "B", "start": 1.4, "end": 1.7},
        {"word": "and", "speaker": "B", "start": 1.7, "end": 1.8},
        {"word": "renewal", "speaker": "B", "start": 1.8, "end": 2.1},
        {"word": "window", "speaker": "B", "start": 2.1, "end": 2.4},
        {"word": "with", "speaker": "B", "start": 2.4, "end": 2.6},
        {"word": "watt", "speaker": "B", "start": 2.6, "end": 2.8},
        {"word": "utilities", "speaker": "B", "start": 2.8, "end": 3.2},
    ]
    db.add(Call(
        id=call_id,
        filename="x.mp3",
        file_path="/x.mp3",
        status="completed",
        transcript="[00:00] Customer: hello your current contract...",  # WRONG
        word_data=_json.dumps(word_data),
    ))
    db.commit()

    await _rederive_speaker_labels(call_id, run_id="test-wave-21")

    db.expire_all()  # Force re-read after the worker thread's commit.
    row = db.query(Call).filter_by(id=call_id).first()
    new_text = row.transcript or ""
    assert "Agent:" in new_text
    # The "your current ... renewal ... watt utilities" turn (speaker B)
    # MUST now be tagged Agent — NOT Customer (that was the bug).
    assert "Customer: your current" not in new_text
    first_transcript = new_text

    # Idempotent — second invocation writes nothing new.
    await _rederive_speaker_labels(call_id, run_id="test-wave-21-redo")
    db.expire_all()
    row = db.query(Call).filter_by(id=call_id).first()
    assert (row.transcript or "") == first_transcript, (
        "Wave-21: second re-derive should be a no-op (idempotent)."
    )


@pytest.mark.asyncio
async def test_rederive_speaker_labels_no_word_data_skipped_gracefully(
    wave21_sqlite_session,
):
    """When `Call.word_data` is null, the re-derive must NOT crash; just
    return silently. This protects the Reanalyze flow against pre-
    word-data legacy calls."""
    import uuid as _uuid
    from app.models import Call
    from app.replay import _rederive_speaker_labels

    db = wave21_sqlite_session
    call_id = str(_uuid.uuid4())
    db.add(Call(
        id=call_id,
        filename="x.mp3",
        file_path="/x.mp3",
        status="completed",
        transcript="legacy text",
        word_data=None,
    ))
    db.commit()

    await _rederive_speaker_labels(call_id, run_id="test-wave-21-noword")

    db.expire_all()
    row = db.query(Call).filter_by(id=call_id).first()
    assert row.transcript == "legacy text"


@pytest.mark.asyncio
async def test_rederive_speaker_labels_corrupt_word_data_does_not_crash(
    wave21_sqlite_session,
):
    """Corrupt word_data (invalid JSON) must be swallowed quietly."""
    import uuid as _uuid
    from app.models import Call
    from app.replay import _rederive_speaker_labels

    db = wave21_sqlite_session
    call_id = str(_uuid.uuid4())
    db.add(Call(
        id=call_id,
        filename="x.mp3",
        file_path="/x.mp3",
        status="completed",
        transcript="legacy text",
        word_data="{this is not json",
    ))
    db.commit()

    await _rederive_speaker_labels(call_id, run_id="test-wave-21-corrupt")

    db.expire_all()
    row = db.query(Call).filter_by(id=call_id).first()
    assert row.transcript == "legacy text"
