import json
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.models import Call, CallCheckpoint
from app.schemas import ComplianceResult, RuleCheckpoint


@pytest.mark.asyncio
async def test_process_call_v1_with_checkpoints(test_db):
    from app.pipeline import process_call

    call = Call(
        id="test-123",
        filename="test.mp3",
        file_path="/tmp/test.mp3",
        file_size=1024,
        status="processing",
    )
    test_db.add(call)
    test_db.commit()

    mock_result = ComplianceResult(
        compliant=True,
        reason="Agent disclosed third-party status",
        excerpt="We are a third party broker",
        checkpoints=[
            RuleCheckpoint(rule="The agent explicitly states the company is a third party", passed=True, excerpt="we are a third party"),
            RuleCheckpoint(rule="The agent states the company is NOT an energy supplier", passed=True, excerpt="We are not British Gas"),
            RuleCheckpoint(rule="The agent identifies as an independent broker", passed=True, excerpt="We act as a broker"),
        ],
    )

    async def fake_transcribe(call_id, audio_path, db):
        # Mimic real _step_transcribe: write transcript onto Call row + commit
        c = db.query(Call).filter_by(id=call_id).first()
        c.transcript = "Agent: We are a third party broker"
        db.commit()
        return {"transcript": "Agent: We are a third party broker", "source": "test"}

    with patch("app.pipeline._step_download_audio", new_callable=AsyncMock) as mock_dl, \
         patch("app.pipeline._step_transcribe", side_effect=fake_transcribe) as mock_transcribe, \
         patch("app.pipeline.detect_supplier", new_callable=AsyncMock) as mock_detect, \
         patch("app.pipeline.analyze_compliance_v1", new_callable=AsyncMock) as mock_analyze:
        mock_dl.return_value = ("/tmp/fake.mp3", None)
        mock_detect.return_value = "Unknown"
        mock_analyze.return_value = mock_result

        await process_call("test-123", "/tmp/test.mp3", test_db)

    updated = test_db.query(Call).filter_by(id="test-123").first()
    assert updated.status == "completed"
    assert updated.compliant is True
    assert updated.transcript == "Agent: We are a third party broker"
    assert updated.completed_at is not None
    assert updated.score == "3/3"

    # Verify CallCheckpoint rows were created
    checkpoints = test_db.query(CallCheckpoint).filter_by(call_id="test-123").all()
    assert len(checkpoints) == 3
    assert all(cp.passed for cp in checkpoints)
    assert checkpoints[0].rule_text == "The agent explicitly states the company is a third party"
    assert checkpoints[0].excerpt == "we are a third party"

    # Verify checkpoint_results JSON was also stored
    assert updated.checkpoint_results is not None
    cp_json = json.loads(updated.checkpoint_results)
    assert len(cp_json) == 3


@pytest.mark.asyncio
async def test_process_call_failure_marks_failed(test_db):
    from app.pipeline import process_call

    call = Call(
        id="test-456",
        filename="bad.mp3",
        file_path="/tmp/bad.mp3",
        file_size=1024,
        status="processing",
    )
    test_db.add(call)
    test_db.commit()

    with patch("app.pipeline._step_download_audio", new_callable=AsyncMock) as mock_dl, \
         patch("app.pipeline._step_transcribe", new_callable=AsyncMock) as mock_transcribe:
        mock_dl.return_value = ("/tmp/fake.mp3", None)
        mock_transcribe.side_effect = Exception("Deepgram API error")

        await process_call("test-456", "/tmp/bad.mp3", test_db)

    updated = test_db.query(Call).filter_by(id="test-456").first()
    assert updated.status == "failed"
    assert "Deepgram API error" in updated.reason

    # No checkpoints should be created on failure
    checkpoints = test_db.query(CallCheckpoint).filter_by(call_id="test-456").all()
    assert len(checkpoints) == 0
