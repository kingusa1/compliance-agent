"""Integration: POST /calls/{id}/reanalyze emits CALL_REANALYZE and writes audit row."""
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


def test_reanalyze_returns_202_when_call_has_transcript(db_session_with_call_with_transcript):
    """db_session_with_call_with_transcript is a fixture seeding a Call row
    with non-null `transcript`, `word_data`, and `script_id`. See conftest.py."""
    call_id = db_session_with_call_with_transcript

    with patch("app.replay.emit_event_async") as mock_emit:
        async def fake_emit(*args, **kwargs):
            return None
        mock_emit.side_effect = fake_emit
        r = client.post(f"/api/calls/{call_id}/reanalyze")

    assert r.status_code == 202
    body = r.json()
    assert body["call_id"] == call_id
    assert "run_id" in body
    mock_emit.assert_called_once()
    # emit_event_async called positionally: (name, data)
    name, payload = mock_emit.call_args.args
    assert name == "call/reanalyze"
    assert payload["call_id"] == call_id


def test_reanalyze_returns_422_when_transcript_missing(db_session_with_call_no_transcript):
    call_id = db_session_with_call_no_transcript
    r = client.post(f"/api/calls/{call_id}/reanalyze")
    assert r.status_code == 422
    assert "transcript" in r.json()["detail"].lower()


def test_reanalyze_returns_404_for_unknown_call_id():
    r = client.post("/api/calls/00000000-0000-0000-0000-000000000000/reanalyze")
    assert r.status_code == 404
