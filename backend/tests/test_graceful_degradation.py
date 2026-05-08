"""
Tests for Task 1.4: Graceful Degradation on Partial Failures

Verifies that:
- A single checkpoint error leaves the call as "completed" with an adjusted score
  (denominator excludes the errored checkpoint)
- A majority of checkpoint errors (>50%) causes the call to become
  "needs_manual_review" rather than "completed"
- All checkpoints erroring also triggers "needs_manual_review"
"""
import json
from unittest.mock import AsyncMock, patch

import pytest

from app.models import Call, CallCheckpoint, Script


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_CHECKPOINTS = json.dumps([
    {
        "section": 1, "name": "Checkpoint 1", "required": "Say X",
        "key_phrases": ["X"], "customer_response_required": False, "strictness": "mandatory",
    },
    {
        "section": 2, "name": "Checkpoint 2", "required": "Say Y",
        "key_phrases": ["Y"], "customer_response_required": False, "strictness": "mandatory",
    },
    {
        "section": 3, "name": "Checkpoint 3", "required": "Say Z",
        "key_phrases": ["Z"], "customer_response_required": False, "strictness": "mandatory",
    },
    {
        "section": 4, "name": "Checkpoint 4", "required": "Say W",
        "key_phrases": ["W"], "customer_response_required": False, "strictness": "mandatory",
    },
])


def _make_script(db, script_id: str, supplier_name: str = "TestSupplier") -> Script:
    script = Script(
        id=script_id,
        supplier_name=supplier_name,
        script_name="Test Script",
        version="1.0",
        mode="meaning_for_meaning",
        checkpoints=SAMPLE_CHECKPOINTS,
        active=True,
    )
    db.add(script)
    db.commit()
    return script


def _make_call(db, call_id: str, script_id: str) -> Call:
    call = Call(
        id=call_id,
        filename="test.mp3",
        file_path=f"/tmp/{call_id}.mp3",
        file_size=1024,
        status="processing",
        script_id=script_id,
    )
    db.add(call)
    db.commit()
    return call


def _make_analyze_result(checkpoint_statuses: list[str]) -> dict:
    """Build the dict that analyze_all_checkpoints would return.

    Evidence strings for pass/partial use text that exists in the mock transcript
    ("We are a third party broker") so verify_checkpoint_results won't downgrade them.
    Fail checkpoints use "NOT FOUND IN TRANSCRIPT" (canonical sentinel).
    """
    results = []
    for i, status in enumerate(checkpoint_statuses, start=1):
        if status == "error":
            evidence = ""
        elif status == "fail":
            evidence = "NOT FOUND IN TRANSCRIPT"
        else:
            # Exact substring of the mock transcript so fuzzy verification passes
            evidence = "We are a third party broker"
        results.append({
            "section": i,
            "name": f"Checkpoint {i}",
            "status": status,
            "evidence": evidence,
            "notes": f"Error in checkpoint {i}" if status == "error" else None,
        })

    error_count = sum(1 for s in checkpoint_statuses if s == "error")
    passed = sum(1 for s in checkpoint_statuses if s == "pass")
    partial = sum(1 for s in checkpoint_statuses if s == "partial")
    failed = sum(1 for s in checkpoint_statuses if s in ("fail", "unverified"))
    non_error_total = len(checkpoint_statuses) - error_count
    score = f"{passed}/{non_error_total}" if non_error_total > 0 else "0/0"

    return {
        "results": results,
        "agent_name": "Test Agent",
        "customer_name": "Test Customer",
        "summary": {
            "total": non_error_total,
            "passed": passed,
            "partial": partial,
            "failed": failed,
            "error": error_count,
            "compliant": failed == 0 and partial == 0 and error_count == 0,
            "score": score,
        },
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_error_call_completes_with_adjusted_score(test_db):
    """1 error out of 4 checkpoints → call completes with adjusted score (denominator=3).

    compliant is False because an errored checkpoint means we can't confirm full compliance.
    """
    from app.pipeline import process_call

    script = _make_script(test_db, "script-001")
    _make_call(test_db, "deg-001", script.id)

    # 4 checkpoints: pass, pass, pass, error  → score should be 3/3
    mock_result = _make_analyze_result(["pass", "pass", "pass", "error"])

    with (
        patch("app.pipeline._step_download_audio", new_callable=AsyncMock) as mock_dl,
        patch("app.pipeline._step_transcribe", new_callable=AsyncMock) as mock_tx,
        patch("app.pipeline.analyze_all_checkpoints", new_callable=AsyncMock) as mock_analyze,
    ):
        mock_dl.return_value = ("/tmp/fake.mp3", None)
        mock_tx.return_value = {"transcript": "Agent: We are a third party broker", "source": "test"}
        mock_analyze.return_value = mock_result

        await process_call("deg-001", "/tmp/deg-001.mp3", test_db, script_id=script.id)

    call = test_db.query(Call).filter_by(id="deg-001").first()
    assert call.status == "completed", f"Expected 'completed', got '{call.status}'"
    assert call.score == "3/3", f"Expected '3/3', got '{call.score}'"
    # compliant=False because error_count > 0 means we can't confirm full compliance
    assert call.compliant is False

    # All 4 checkpoint rows should still be persisted
    checkpoints = test_db.query(CallCheckpoint).filter_by(call_id="deg-001").all()
    assert len(checkpoints) == 4


@pytest.mark.asyncio
async def test_single_error_with_failures_completes_with_adjusted_score(test_db):
    """1 error + 1 fail out of 4 → call completes, score = 2/3 (error excluded)."""
    from app.pipeline import process_call

    script = _make_script(test_db, "script-002")
    _make_call(test_db, "deg-002", script.id)

    # pass, pass, fail, error → denominator = 3, passed = 2 → "2/3"
    mock_result = _make_analyze_result(["pass", "pass", "fail", "error"])

    with (
        patch("app.pipeline._step_download_audio", new_callable=AsyncMock) as mock_dl,
        patch("app.pipeline._step_transcribe", new_callable=AsyncMock) as mock_tx,
        patch("app.pipeline.analyze_all_checkpoints", new_callable=AsyncMock) as mock_analyze,
    ):
        mock_dl.return_value = ("/tmp/fake.mp3", None)
        mock_tx.return_value = {"transcript": "Agent: We are a third party broker", "source": "test"}
        mock_analyze.return_value = mock_result

        await process_call("deg-002", "/tmp/deg-002.mp3", test_db, script_id=script.id)

    call = test_db.query(Call).filter_by(id="deg-002").first()
    assert call.status == "completed"
    assert call.score == "2/3", f"Expected '2/3', got '{call.score}'"
    assert call.compliant is False


@pytest.mark.asyncio
async def test_majority_errors_triggers_manual_review(test_db):
    """3 errors out of 4 checkpoints (>50%) → call gets 'needs_manual_review'."""
    from app.pipeline import process_call

    script = _make_script(test_db, "script-003")
    _make_call(test_db, "deg-003", script.id)

    # pass, error, error, error → 3/4 = 75% errors → needs_manual_review
    mock_result = _make_analyze_result(["pass", "error", "error", "error"])

    with (
        patch("app.pipeline._step_download_audio", new_callable=AsyncMock) as mock_dl,
        patch("app.pipeline._step_transcribe", new_callable=AsyncMock) as mock_tx,
        patch("app.pipeline.analyze_all_checkpoints", new_callable=AsyncMock) as mock_analyze,
    ):
        mock_dl.return_value = ("/tmp/fake.mp3", None)
        mock_tx.return_value = {"transcript": "Agent: We are a third party broker", "source": "test"}
        mock_analyze.return_value = mock_result

        await process_call("deg-003", "/tmp/deg-003.mp3", test_db, script_id=script.id)

    call = test_db.query(Call).filter_by(id="deg-003").first()
    assert call.status == "needs_manual_review", (
        f"Expected 'needs_manual_review', got '{call.status}'"
    )
    assert call.compliant is False
    assert "manual review" in call.reason.lower()
    assert "3" in call.reason  # error count mentioned
    assert call.completed_at is not None  # completed_at still set


@pytest.mark.asyncio
async def test_all_errors_triggers_manual_review(test_db):
    """All 4 checkpoints error → call gets 'needs_manual_review'."""
    from app.pipeline import process_call

    script = _make_script(test_db, "script-004")
    _make_call(test_db, "deg-004", script.id)

    mock_result = _make_analyze_result(["error", "error", "error", "error"])

    with (
        patch("app.pipeline._step_download_audio", new_callable=AsyncMock) as mock_dl,
        patch("app.pipeline._step_transcribe", new_callable=AsyncMock) as mock_tx,
        patch("app.pipeline.analyze_all_checkpoints", new_callable=AsyncMock) as mock_analyze,
    ):
        mock_dl.return_value = ("/tmp/fake.mp3", None)
        mock_tx.return_value = {"transcript": "Agent: We are a third party broker", "source": "test"}
        mock_analyze.return_value = mock_result

        await process_call("deg-004", "/tmp/deg-004.mp3", test_db, script_id=script.id)

    call = test_db.query(Call).filter_by(id="deg-004").first()
    assert call.status == "needs_manual_review", (
        f"Expected 'needs_manual_review', got '{call.status}'"
    )
    assert call.compliant is False
    assert "manual review" in call.reason.lower()
    assert call.completed_at is not None


@pytest.mark.asyncio
async def test_exactly_half_errors_does_not_trigger_manual_review(test_db):
    """Exactly 50% errors (2 of 4) is NOT >50% → call still completes."""
    from app.pipeline import process_call

    script = _make_script(test_db, "script-005")
    _make_call(test_db, "deg-005", script.id)

    # pass, pass, error, error → 2/4 = exactly 50% — NOT more than 50%
    mock_result = _make_analyze_result(["pass", "pass", "error", "error"])

    with (
        patch("app.pipeline._step_download_audio", new_callable=AsyncMock) as mock_dl,
        patch("app.pipeline._step_transcribe", new_callable=AsyncMock) as mock_tx,
        patch("app.pipeline.analyze_all_checkpoints", new_callable=AsyncMock) as mock_analyze,
    ):
        mock_dl.return_value = ("/tmp/fake.mp3", None)
        mock_tx.return_value = {"transcript": "Agent: We are a third party broker", "source": "test"}
        mock_analyze.return_value = mock_result

        await process_call("deg-005", "/tmp/deg-005.mp3", test_db, script_id=script.id)

    call = test_db.query(Call).filter_by(id="deg-005").first()
    assert call.status == "completed", (
        f"Expected 'completed' (exactly 50% is not >50%), got '{call.status}'"
    )
    assert call.score == "2/2"  # 2 passed out of 2 non-error


@pytest.mark.asyncio
async def test_no_errors_call_completes_normally(test_db):
    """Sanity check: zero errors → call completes with full denominator."""
    from app.pipeline import process_call

    script = _make_script(test_db, "script-006")
    _make_call(test_db, "deg-006", script.id)

    mock_result = _make_analyze_result(["pass", "pass", "fail", "pass"])

    with (
        patch("app.pipeline._step_download_audio", new_callable=AsyncMock) as mock_dl,
        patch("app.pipeline._step_transcribe", new_callable=AsyncMock) as mock_tx,
        patch("app.pipeline.analyze_all_checkpoints", new_callable=AsyncMock) as mock_analyze,
    ):
        mock_dl.return_value = ("/tmp/fake.mp3", None)
        mock_tx.return_value = {"transcript": "Agent: We are a third party broker", "source": "test"}
        mock_analyze.return_value = mock_result

        await process_call("deg-006", "/tmp/deg-006.mp3", test_db, script_id=script.id)

    call = test_db.query(Call).filter_by(id="deg-006").first()
    assert call.status == "completed"
    assert call.score == "3/4"
    assert call.compliant is False

    checkpoints = test_db.query(CallCheckpoint).filter_by(call_id="deg-006").all()
    assert len(checkpoints) == 4
