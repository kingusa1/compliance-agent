"""
Integration tests for the full compliance analysis pipeline.

These tests exercise process_call() end-to-end using mocked external APIs
(transcription, LLM). They cover:
  1. Fully compliant call  → all V2 checkpoints pass
  2. Non-compliant call    → specific V2 checkpoints fail
  3. Unknown supplier      → fallback to V1 analysis path
  4. LLM timeout on one checkpoint → graceful degradation (pipeline still completes)
"""

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import Call, CallCheckpoint, Script


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture_transcript() -> str:
    return (FIXTURES_DIR / "sample_transcript.txt").read_text()


def _load_fixture_script() -> dict:
    return json.loads((FIXTURES_DIR / "sample_script.json").read_text())


def _make_script(db, supplier_name: str = "Energy Solutions", active: bool = True) -> Script:
    """Insert a Script record built from the sample_script.json fixture."""
    raw = _load_fixture_script()
    script = Script(
        supplier_name=supplier_name,
        script_name=raw["script_name"],
        version=raw.get("version"),
        mode=raw.get("mode", "meaning_for_meaning"),
        checkpoints=json.dumps(raw["checkpoints"]),
        active=active,
    )
    db.add(script)
    db.commit()
    db.refresh(script)
    return script


def _make_call(db, call_id: str = "call-001", file_path: str = "/tmp/test.mp3") -> Call:
    """Insert a Call record in 'processing' state."""
    call = Call(
        id=call_id,
        filename="test.mp3",
        file_path=file_path,
        file_size=1024,
        status="processing",
    )
    db.add(call)
    db.commit()
    return call


def _v2_analyzer_result(
    checkpoints: list[dict],
    agent_name: str = "Sarah",
    customer_name: str = "Unknown",
) -> dict:
    """Build a mock return value for analyze_all_checkpoints().

    Mirrors the severity-weighted summary the real analyzer returns:
    bucket ∈ {pass, coaching, review, blocked}; compliant is derived from
    the bucket (pass/coaching → True). Default severity is 'medium' when
    not set on an input checkpoint dict — same fallback the real analyzer
    uses.
    """
    results = []
    for cp in checkpoints:
        results.append({
            **cp,
            "agent_name": agent_name,
            "customer_name": customer_name,
            "verified": True,
            "similarity": 1.0,
        })

    non_error = [r for r in results if r["status"] != "error"]
    total = len(non_error)
    passed = sum(1 for r in non_error if r["status"] == "pass")
    partial = sum(1 for r in non_error if r["status"] == "partial")
    failed = sum(1 for r in non_error if r["status"] in ("fail", "unverified"))
    error_count = sum(1 for r in results if r["status"] == "error")

    def _sev(cp: dict) -> str:
        s = str(cp.get("severity") or "medium").lower()
        return s if s in {"critical", "high", "medium", "low", "info"} else "medium"

    breached = [r for r in non_error if r["status"] in ("fail", "unverified", "partial")]
    critical_hits = [r for r in breached if _sev(r) == "critical"]
    high_hits = [r for r in breached if _sev(r) == "high"]
    medium_hits = [r for r in breached if _sev(r) in ("medium", "low", "info")]

    if critical_hits:
        bucket, compliant = "blocked", False
    elif high_hits:
        bucket, compliant = "review", False
    elif medium_hits:
        bucket, compliant = "coaching", True
    else:
        bucket, compliant = "pass", total > 0

    return {
        "results": results,
        "agent_name": agent_name,
        "customer_name": customer_name,
        "summary": {
            "total": total,
            "passed": passed,
            "partial": partial,
            "failed": failed,
            "error": error_count,
            "compliant": compliant,
            "bucket": bucket,
            "critical_breaches": len(critical_hits),
            "high_breaches": len(high_hits),
            "medium_breaches": len(medium_hits),
            "score": f"{passed}/{total}" if total > 0 else "0/0",
        },
    }


def _mock_llm_http_response(payload: dict) -> MagicMock:
    """Wrap a payload dict in a mock httpx response."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": json.dumps(payload)}}]
    }
    mock_response.raise_for_status = MagicMock()
    return mock_response


def _patch_httpx_client(mock_response: MagicMock):
    """Return a context manager that patches httpx.AsyncClient inside app.analysis."""
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return patch("app.analysis.httpx.AsyncClient", return_value=mock_client)


# ---------------------------------------------------------------------------
# Test 1 — Fully compliant call (V2 path, all checkpoints pass)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_integration_compliant_call_v2(test_db):
    """
    Upload a known compliant call → verify all V2 checkpoints pass.

    Pipeline: transcribe (mocked) → detect supplier (mocked) → V2 analysis
    (mocked LLM) → verify quotes → save to DB → check final score.
    """
    from app.pipeline import process_call

    transcript = _load_fixture_transcript()
    script = _make_script(test_db, supplier_name="Energy Solutions")
    call = _make_call(test_db, call_id="integ-001")

    # All 4 checkpoints pass — quotes extracted from the actual fixture transcript
    all_pass_checkpoints = [
        {
            "section": 1,
            "name": "Agent explicitly states the company is a third party",
            "status": "pass",
            "evidence": "we are an independent energy broker and a third party",
            "notes": None,
        },
        {
            "section": 2,
            "name": "Agent states the company is NOT an energy supplier",
            "status": "pass",
            "evidence": "We are not an energy supplier like British Gas or E.ON Next",
            "notes": None,
        },
        {
            "section": 3,
            "name": "Agent identifies as an independent broker or intermediary",
            "status": "pass",
            "evidence": "We act as a broker — an intermediary — between you and the energy suppliers",
            "notes": None,
        },
        {
            "section": 4,
            "name": "Agent discloses how they are remunerated",
            "status": "pass",
            "evidence": "We are paid a referral fee by the supplier if you switch",
            "notes": None,
        },
    ]

    mock_result = _v2_analyzer_result(all_pass_checkpoints)

    with patch("app.pipeline._step_download_audio", new_callable=AsyncMock) as mock_dl, \
         patch("app.pipeline._step_transcribe", new_callable=AsyncMock) as mock_transcribe, \
         patch("app.pipeline.detect_supplier", new_callable=AsyncMock) as mock_detect, \
         patch("app.pipeline.analyze_all_checkpoints", new_callable=AsyncMock) as mock_analyze:

        mock_dl.return_value = ("/tmp/fake.mp3", None)
        mock_transcribe.return_value = {"transcript": transcript, "source": "test"}
        mock_detect.return_value = "Energy Solutions"
        mock_analyze.return_value = mock_result

        await process_call("integ-001", "/tmp/test.mp3", test_db, script_id=script.id)

    updated = test_db.query(Call).filter_by(id="integ-001").first()

    # Status & compliance
    assert updated.status == "completed", f"Expected 'completed', got '{updated.status}'"
    assert updated.compliant is True, "Expected call to be fully compliant"
    assert updated.completed_at is not None

    # Score should be 4/4
    assert updated.score == "4/4", f"Expected '4/4', got '{updated.score}'"

    # Names extracted from LLM response
    assert updated.agent_name == "Sarah"

    # CallCheckpoint rows created
    checkpoints = test_db.query(CallCheckpoint).filter_by(call_id="integ-001").all()
    assert len(checkpoints) == 4
    assert all(cp.passed for cp in checkpoints), "All checkpoint rows should be passed=True"

    # JSON blob also persisted
    cp_json = json.loads(updated.checkpoint_results)
    assert len(cp_json) == 4
    assert all(cp["status"] == "pass" for cp in cp_json)


# ---------------------------------------------------------------------------
# Test 2 — Non-compliant call (V2 path, specific checkpoints fail)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_integration_non_compliant_call_v2(test_db):
    """
    Upload a known non-compliant call → verify specific checkpoints fail.

    Sections 3 and 4 deliberately not covered by the simulated transcript.
    """
    from app.pipeline import process_call

    # Minimal transcript that covers only sections 1 & 2
    partial_transcript = (
        "[00:00] Agent: Hi, I'm calling from Energy Solutions.\n"
        "[00:05] Agent: We are a third party and we are not an energy supplier.\n"
        "[00:10] Customer: Okay, tell me more.\n"
    )

    script = _make_script(test_db, supplier_name="Energy Solutions")
    call = _make_call(test_db, call_id="integ-002")

    mixed_checkpoints = [
        {
            "section": 1,
            "name": "Agent explicitly states the company is a third party",
            "status": "pass",
            "evidence": "We are a third party",
            "notes": None,
        },
        {
            "section": 2,
            "name": "Agent states the company is NOT an energy supplier",
            "status": "pass",
            "evidence": "we are not an energy supplier",
            "notes": None,
        },
        {
            "section": 3,
            "name": "Agent identifies as an independent broker or intermediary",
            "status": "fail",
            "evidence": "NOT FOUND IN TRANSCRIPT",
            "notes": "Agent did not describe their role as a broker or intermediary.",
        },
        {
            "section": 4,
            "name": "Agent discloses how they are remunerated",
            "status": "fail",
            "evidence": "NOT FOUND IN TRANSCRIPT",
            "notes": "Agent did not mention referral fee or commission.",
        },
    ]

    mock_result = _v2_analyzer_result(mixed_checkpoints)

    with patch("app.pipeline._step_download_audio", new_callable=AsyncMock) as mock_dl, \
         patch("app.pipeline._step_transcribe", new_callable=AsyncMock) as mock_transcribe, \
         patch("app.pipeline.detect_supplier", new_callable=AsyncMock) as mock_detect, \
         patch("app.pipeline.analyze_all_checkpoints", new_callable=AsyncMock) as mock_analyze:

        mock_dl.return_value = ("/tmp/fake.mp3", None)
        mock_transcribe.return_value = {"transcript": partial_transcript, "source": "test"}
        mock_detect.return_value = "Energy Solutions"
        mock_analyze.return_value = mock_result

        await process_call("integ-002", "/tmp/test.mp3", test_db, script_id=script.id)

    updated = test_db.query(Call).filter_by(id="integ-002").first()

    assert updated.status == "completed"
    assert updated.compliant is False, "Call with failed checkpoints must not be compliant"
    assert updated.score == "2/4", f"Expected '2/4', got '{updated.score}'"

    # "2 checkpoint(s) missed" should appear in reason
    assert "2" in updated.reason

    checkpoints = test_db.query(CallCheckpoint).filter_by(call_id="integ-002").all()
    assert len(checkpoints) == 4

    passed = [cp for cp in checkpoints if cp.passed]
    failed = [cp for cp in checkpoints if not cp.passed]
    assert len(passed) == 2
    assert len(failed) == 2

    # Verify the specific sections that failed
    failed_names = {cp.rule_text for cp in failed}
    assert "Agent identifies as an independent broker or intermediary" in failed_names
    assert "Agent discloses how they are remunerated" in failed_names


# ---------------------------------------------------------------------------
# Test 3 — Wrong supplier detection → fallback to V1
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_integration_unknown_supplier_fallback_v1(test_db):
    """
    When supplier detection returns an unknown name and no script_id is provided,
    the pipeline falls back to V1 single-rule analysis.

    Verifies:
    - call.detected_supplier is set to the returned (unrecognised) value
    - V1 result is stored correctly
    - score is calculated from V1 checkpoints
    """
    from app.pipeline import process_call

    transcript = (
        "[00:00] Agent: Hello, I'm calling from Acme Energy.\n"
        "[00:05] Agent: We are a third party broker, not an energy supplier.\n"
        "[00:10] Customer: That's fine.\n"
    )

    # No script for "Acme Energy" in DB → V1 fallback
    call = _make_call(test_db, call_id="integ-003")

    v1_payload = {
        "compliant": True,
        "reason": "Agent correctly identified as third-party broker.",
        "excerpt": "We are a third party broker, not an energy supplier",
        "agent_name": "Unknown",
        "customer_name": "Unknown",
        "checkpoints": [
            {
                "rule": "The agent explicitly states the company is a third party",
                "passed": True,
                "excerpt": "We are a third party broker",
            },
            {
                "rule": "The agent states the company is NOT an energy supplier",
                "passed": True,
                "excerpt": "not an energy supplier",
            },
            {
                "rule": "The agent identifies themselves/company as an independent broker or intermediary",
                "passed": True,
                "excerpt": "We are a third party broker",
            },
        ],
    }
    mock_http = _mock_llm_http_response(v1_payload)

    with _patch_httpx_client(mock_http), \
         patch("app.pipeline._step_download_audio", new_callable=AsyncMock) as mock_dl, \
         patch("app.pipeline._step_transcribe", new_callable=AsyncMock) as mock_transcribe, \
         patch("app.pipeline.detect_supplier", new_callable=AsyncMock) as mock_detect:

        mock_dl.return_value = ("/tmp/fake.mp3", None)
        mock_transcribe.return_value = {"transcript": transcript, "source": "test"}
        # LLM returns an unrecognised supplier name
        mock_detect.return_value = "Acme Energy"

        # No script_id provided — pipeline must auto-detect and fall back to V1
        await process_call("integ-003", "/tmp/test.mp3", test_db)

    updated = test_db.query(Call).filter_by(id="integ-003").first()

    assert updated.status == "completed"
    assert updated.detected_supplier == "Acme Energy"

    # V1 path should have created 3 checkpoint rows
    checkpoints = test_db.query(CallCheckpoint).filter_by(call_id="integ-003").all()
    assert len(checkpoints) == 3
    assert all(cp.passed for cp in checkpoints)

    # Score computed from V1 checkpoints
    assert updated.score == "3/3"
    assert updated.compliant is True

    # excerpt comes from the V1 result (not verified quotes)
    assert updated.excerpt is not None


# ---------------------------------------------------------------------------
# Test 4 — LLM timeout on analysis → pipeline marks call as failed (graceful)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_integration_llm_timeout_graceful_degradation(test_db):
    """
    When the LLM call raises a timeout/exception during analysis, the pipeline
    catches it, marks the call as 'failed', records the error in call.reason,
    and does NOT leave any orphaned checkpoint rows.
    """
    from app.pipeline import process_call

    transcript = _load_fixture_transcript()
    script = _make_script(test_db, supplier_name="Energy Solutions")
    call = _make_call(test_db, call_id="integ-004")

    with patch("app.pipeline._step_download_audio", new_callable=AsyncMock) as mock_dl, \
         patch("app.pipeline._step_transcribe", new_callable=AsyncMock) as mock_transcribe, \
         patch("app.pipeline.detect_supplier", new_callable=AsyncMock) as mock_detect, \
         patch("app.pipeline.analyze_all_checkpoints", new_callable=AsyncMock) as mock_analyze:

        mock_dl.return_value = ("/tmp/fake.mp3", None)
        mock_transcribe.return_value = {"transcript": transcript, "source": "test"}
        mock_detect.return_value = "Energy Solutions"
        # Simulate a timeout on the parallel analyzer
        mock_analyze.side_effect = TimeoutError("LLM request timed out after 90s")

        await process_call("integ-004", "/tmp/test.mp3", test_db, script_id=script.id)

    updated = test_db.query(Call).filter_by(id="integ-004").first()

    # Pipeline must NOT crash — call is marked failed
    assert updated.status == "failed", f"Expected 'failed', got '{updated.status}'"

    # Error reason should be recorded
    assert updated.reason is not None
    assert "timed out" in updated.reason.lower() or "Processing error" in updated.reason

    # No partial checkpoint rows should exist
    checkpoints = test_db.query(CallCheckpoint).filter_by(call_id="integ-004").all()
    assert len(checkpoints) == 0, "No checkpoint rows should be written on pipeline failure"

    # Call should NOT be marked compliant
    assert updated.compliant is None


# ---------------------------------------------------------------------------
# Test 5 — V2 with partial checkpoint → score and reason reflect partial state
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_integration_partial_checkpoint_v2(test_db):
    """
    When one checkpoint is 'partial', it counts against full compliance.
    call.compliant must be False and the score must reflect partial as a miss.
    """
    from app.pipeline import process_call

    transcript = _load_fixture_transcript()
    script = _make_script(test_db, supplier_name="Energy Solutions")
    call = _make_call(test_db, call_id="integ-005")

    # Mark the partial checkpoint as severity=high so under the
    # severity-weighted analyzer it lands in the 'review' bucket (compliant
    # is False, reason mentions a High-severity breach). Same intent as the
    # legacy binary rule "partial counts as a miss" — just expressed in the
    # severity language the post-2026-05-10 analyzer speaks.
    partial_checkpoints = [
        {
            "section": 1,
            "name": "Agent explicitly states the company is a third party",
            "status": "pass",
            "evidence": "we are an independent energy broker and a third party",
            "notes": None,
            "severity": "high",
        },
        {
            "section": 2,
            "name": "Agent states the company is NOT an energy supplier",
            "status": "pass",
            "evidence": "We are not an energy supplier like British Gas or E.ON Next",
            "notes": None,
            "severity": "high",
        },
        {
            "section": 3,
            "name": "Agent identifies as an independent broker or intermediary",
            "status": "partial",
            "evidence": "We act as a broker",
            "notes": "Agent mentioned broker but did not say 'intermediary' or 'comparison service'.",
            "severity": "high",
        },
        {
            "section": 4,
            "name": "Agent discloses how they are remunerated",
            "status": "pass",
            "evidence": "We are paid a referral fee by the supplier if you switch",
            "notes": None,
            "severity": "high",
        },
    ]

    mock_result = _v2_analyzer_result(partial_checkpoints)

    with patch("app.pipeline._step_download_audio", new_callable=AsyncMock) as mock_dl, \
         patch("app.pipeline._step_transcribe", new_callable=AsyncMock) as mock_transcribe, \
         patch("app.pipeline.detect_supplier", new_callable=AsyncMock) as mock_detect, \
         patch("app.pipeline.analyze_all_checkpoints", new_callable=AsyncMock) as mock_analyze:

        mock_dl.return_value = ("/tmp/fake.mp3", None)
        mock_transcribe.return_value = {"transcript": transcript, "source": "test"}
        mock_detect.return_value = "Energy Solutions"
        mock_analyze.return_value = mock_result

        await process_call("integ-005", "/tmp/test.mp3", test_db, script_id=script.id)

    updated = test_db.query(Call).filter_by(id="integ-005").first()

    assert updated.status == "completed"
    # 3 pass + 1 partial (severity=high) → review bucket → not compliant
    assert updated.compliant is False
    # Score counts only full passes
    assert updated.score == "3/4"
    # New reason format surfaces the score + a High-severity breach call-out
    assert "3/4" in updated.reason
    assert "high" in updated.reason.lower()

    checkpoints = test_db.query(CallCheckpoint).filter_by(call_id="integ-005").all()
    assert len(checkpoints) == 4


# ---------------------------------------------------------------------------
# Test 6 — script_id takes priority over auto-detect
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_integration_explicit_script_id_skips_detection(test_db):
    """
    When script_id is provided, the pipeline uses it directly and should NOT
    call detect_supplier at all. Even if detect_supplier were called, the
    explicit script should win.
    """
    from app.pipeline import process_call

    transcript = _load_fixture_transcript()
    script = _make_script(test_db, supplier_name="Energy Solutions")
    call = _make_call(test_db, call_id="integ-006")

    all_pass_checkpoints = [
        {
            "section": 1,
            "name": "Agent explicitly states the company is a third party",
            "status": "pass",
            "evidence": "we are an independent energy broker and a third party",
            "notes": None,
        },
        {
            "section": 2,
            "name": "Agent states the company is NOT an energy supplier",
            "status": "pass",
            "evidence": "We are not an energy supplier like British Gas or E.ON Next",
            "notes": None,
        },
        {
            "section": 3,
            "name": "Agent identifies as an independent broker or intermediary",
            "status": "pass",
            "evidence": "We act as a broker — an intermediary — between you and the energy suppliers",
            "notes": None,
        },
        {
            "section": 4,
            "name": "Agent discloses how they are remunerated",
            "status": "pass",
            "evidence": "We are paid a referral fee by the supplier if you switch",
            "notes": None,
        },
    ]

    mock_result = _v2_analyzer_result(all_pass_checkpoints)

    with patch("app.pipeline._step_download_audio", new_callable=AsyncMock) as mock_dl, \
         patch("app.pipeline._step_transcribe", new_callable=AsyncMock) as mock_transcribe, \
         patch("app.pipeline.detect_supplier", new_callable=AsyncMock) as mock_detect, \
         patch("app.pipeline.analyze_all_checkpoints", new_callable=AsyncMock) as mock_analyze:

        mock_dl.return_value = ("/tmp/fake.mp3", None)
        mock_transcribe.return_value = {"transcript": transcript, "source": "test"}
        # detect_supplier should not be called when script_id is supplied
        mock_detect.return_value = "Should Not Be Called"
        mock_analyze.return_value = mock_result

        await process_call("integ-006", "/tmp/test.mp3", test_db, script_id=script.id)

    # detect_supplier must NOT have been called
    mock_detect.assert_not_called()

    updated = test_db.query(Call).filter_by(id="integ-006").first()
    assert updated.status == "completed"
    assert updated.script_id == script.id
    assert updated.compliant is True
    assert updated.score == "4/4"
