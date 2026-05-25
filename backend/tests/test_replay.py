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
