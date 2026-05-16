"""Tests for per-checkpoint parallel analysis (Task 1.1)."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.checkpoint_analyzer import (
    analyze_all_checkpoints,
    analyze_single_checkpoint,
)


SAMPLE_TRANSCRIPT = (
    "Agent: Good morning, my name is Sarah calling from Energy Solutions. "
    "I want to be upfront with you - we are an independent energy broker and a third party. "
    "We are not an energy supplier like British Gas or E.ON Next. "
    "We compare deals across multiple suppliers to find you the best rate.\n"
    "Customer: Oh okay, so you're not from British Gas then?\n"
    "Agent: No, not at all. We are a completely separate company."
)

SAMPLE_CHECKPOINTS = [
    {
        "section": 1,
        "name": "Agent states company is a third party",
        "required": "Agent must explicitly state the company is a third party",
        "key_phrases": ["third party", "third-party"],
        "strictness": "mandatory",
        "customer_response_required": False,
    },
    {
        "section": 2,
        "name": "Agent states company is NOT an energy supplier",
        "required": "Agent must state they are not an energy supplier",
        "key_phrases": ["not an energy supplier", "not a supplier"],
        "strictness": "mandatory",
        "customer_response_required": False,
    },
    {
        "section": 3,
        "name": "Agent identifies as independent broker",
        "required": "Agent must identify as an independent broker or intermediary",
        "key_phrases": ["independent broker", "intermediary"],
        "strictness": "mandatory",
        "customer_response_required": False,
    },
]


def _array_response(payloads: list[dict]) -> MagicMock:
    """Build a mock LLM response whose content is a JSON ARRAY (one entry per
    checkpoint in the batch). The batched analyzer iterates over the array,
    so this is the canonical mock shape post-D02 refactor.
    """
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": json.dumps(payloads)}}]
    }
    mock_response.raise_for_status = MagicMock()
    return mock_response


def _mock_llm_response(payload: dict) -> MagicMock:
    """Create a mock httpx response with the given JSON payload."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": json.dumps(payload)}}]
    }
    mock_response.raise_for_status = MagicMock()
    return mock_response


def _patch_llm(mock_response):
    """Patch httpx.AsyncClient to return the given mock response."""
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return patch("app.analysis.httpx.AsyncClient", return_value=mock_client)


# --- analyze_single_checkpoint tests ---


@pytest.mark.asyncio
async def test_single_checkpoint_pass():
    """A passing checkpoint returns status pass with verified evidence."""
    payload = {
        "status": "pass",
        "evidence": "we are an independent energy broker and a third party",
        "notes": None,
        "agent_name": "Sarah",
        "customer_name": "Unknown",
    }
    mock_response = _mock_llm_response(payload)

    with _patch_llm(mock_response):
        result = await analyze_single_checkpoint(
            SAMPLE_TRANSCRIPT, SAMPLE_CHECKPOINTS[0], "meaning_for_meaning"
        )

    assert result["status"] == "pass"
    assert result["section"] == 1
    assert result["name"] == "Agent states company is a third party"
    assert result["agent_name"] == "Sarah"
    assert result["verified"] is True


@pytest.mark.asyncio
async def test_single_checkpoint_fail():
    """A failing checkpoint returns status fail."""
    payload = {
        "status": "fail",
        "evidence": "AGENT IGNORED THIS CHECKPOINT — not mentioned anywhere in the call",
        "notes": "Agent never stated they are a third party",
        "agent_name": "Unknown",
        "customer_name": "Unknown",
    }
    mock_response = _mock_llm_response(payload)

    with _patch_llm(mock_response):
        result = await analyze_single_checkpoint(
            SAMPLE_TRANSCRIPT, SAMPLE_CHECKPOINTS[0], "meaning_for_meaning"
        )

    assert result["status"] == "fail"
    assert result["notes"] is not None


@pytest.mark.asyncio
async def test_single_checkpoint_partial():
    """A partial checkpoint returns status partial."""
    payload = {
        "status": "partial",
        "evidence": "we are an independent energy broker",
        "notes": "Did not explicitly say 'third party'",
        "agent_name": "Sarah",
        "customer_name": "Unknown",
    }
    mock_response = _mock_llm_response(payload)

    with _patch_llm(mock_response):
        result = await analyze_single_checkpoint(
            SAMPLE_TRANSCRIPT, SAMPLE_CHECKPOINTS[0], "meaning_for_meaning"
        )

    # Evidence IS in the transcript so should verify
    assert result["status"] == "partial"
    assert result["verified"] is True


@pytest.mark.asyncio
async def test_single_checkpoint_unverified_quote():
    """When the LLM fabricates a quote, status becomes unverified."""
    payload = {
        "status": "pass",
        "evidence": "This quote is completely fabricated and not in the transcript at all",
        "notes": None,
        "agent_name": "Sarah",
        "customer_name": "Unknown",
    }
    mock_response = _mock_llm_response(payload)

    with _patch_llm(mock_response):
        result = await analyze_single_checkpoint(
            SAMPLE_TRANSCRIPT, SAMPLE_CHECKPOINTS[0], "meaning_for_meaning"
        )

    assert result["status"] == "unverified"
    assert result["verified"] is False
    # Post-D02 wording: notes mention low similarity rather than the old
    # "QUOTE NOT VERIFIED" header. Either the % match or the "needs human
    # review" hint is sufficient evidence the verifier downgraded.
    notes = (result["notes"] or "").lower()
    assert "needs human review" in notes or "similarity" in notes


@pytest.mark.asyncio
async def test_single_checkpoint_llm_exception():
    """When the LLM call raises an exception, checkpoint gets error status."""
    mock_client = AsyncMock()
    mock_client.post.side_effect = Exception("API connection failed")
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("app.analysis.httpx.AsyncClient", return_value=mock_client):
        result = await analyze_single_checkpoint(
            SAMPLE_TRANSCRIPT, SAMPLE_CHECKPOINTS[0], "meaning_for_meaning"
        )

    assert result["status"] == "error"
    assert "API connection failed" in result["evidence"]
    assert result["verified"] is False


@pytest.mark.asyncio
async def test_single_checkpoint_invalid_json():
    """When the LLM returns invalid JSON, checkpoint gets error status."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "not valid json at all"}}]
    }
    mock_response.raise_for_status = MagicMock()

    with _patch_llm(mock_response):
        result = await analyze_single_checkpoint(
            SAMPLE_TRANSCRIPT, SAMPLE_CHECKPOINTS[0], "meaning_for_meaning"
        )

    assert result["status"] == "error"
    assert result["verified"] is False


@pytest.mark.asyncio
async def test_single_checkpoint_missing_fields_defaults():
    """When LLM response is missing fields, sensible defaults are used."""
    payload = {
        "status": "pass",
        "evidence": "we are an independent energy broker and a third party",
    }
    mock_response = _mock_llm_response(payload)

    with _patch_llm(mock_response):
        result = await analyze_single_checkpoint(
            SAMPLE_TRANSCRIPT, SAMPLE_CHECKPOINTS[0], "meaning_for_meaning"
        )

    assert result["status"] == "pass"
    assert result["agent_name"] == "Unknown"
    assert result["customer_name"] == "Unknown"
    assert result["notes"] is None


# --- analyze_all_checkpoints tests ---


@pytest.mark.asyncio
async def test_all_checkpoints_parallel_all_pass():
    """All checkpoints pass: compliant=True, correct score.

    Post-D02 the analyzer batches checkpoints (BATCH_SIZE=6) per strictness
    group and the LLM is expected to return a JSON ARRAY (one entry per
    checkpoint in the batch). With SAMPLE_CHECKPOINTS now all-mandatory,
    we have one batch of 3 → mock returns one array of 3 pass payloads.
    """
    pass_entry = {
        "status": "pass",
        "evidence": "we are an independent energy broker and a third party",
        "notes": None,
        "agent_name": "Sarah",
        "customer_name": "Unknown",
    }
    mock_response = _array_response([pass_entry, pass_entry, pass_entry])

    with _patch_llm(mock_response):
        result = await analyze_all_checkpoints(
            SAMPLE_TRANSCRIPT, SAMPLE_CHECKPOINTS, "meaning_for_meaning"
        )

    assert len(result["results"]) == 3
    assert result["summary"]["passed"] == 3
    assert result["summary"]["failed"] == 0
    assert result["summary"]["partial"] == 0
    assert result["summary"]["error"] == 0
    assert result["summary"]["compliant"] is True
    assert result["summary"]["score"] == "3/3"
    assert result["agent_name"] == "Sarah"


@pytest.mark.asyncio
async def test_all_checkpoints_mixed_results():
    """Mixed pass/fail/partial results with the severity-weighted verdict
    introduced 2026-05-10 + the 2026-05-15 medium-only pass-rate gate.

    SAMPLE_CHECKPOINTS carries no explicit severity — default is 'medium',
    so a fail + a partial both land in the ``medium_hits`` bucket. Under
    the mapping that's normally ``coaching`` (compliant=True with a
    coaching note logged). But commit ``a83e441`` added a guard: when
    ALL breaches are medium AND pass-rate < 50%, escalate to ``review``
    (compliant=False) so cases like Andrew's LOA 0/11-all-medium stop
    rendering as "coaching/compliant". This test exercises that gate
    (1/3 = 33% pass rate → escalation fires).
    """
    async def mock_call_llm(prompt, timeout=60.0):
        return json.dumps([
            {
                "status": "pass",
                "evidence": "we are an independent energy broker and a third party",
                "notes": None,
                "agent_name": "Sarah",
                "customer_name": "Unknown",
            },
            {
                "status": "fail",
                "evidence": "AGENT IGNORED THIS CHECKPOINT — not mentioned anywhere in the call",
                "notes": "Not found",
                "agent_name": "Unknown",
                "customer_name": "Unknown",
            },
            {
                "status": "partial",
                "evidence": "we are an independent energy broker",
                "notes": "Missing exact wording",
                "agent_name": "Sarah",
                "customer_name": "Unknown",
            },
        ])

    with patch("app.checkpoint_analyzer._call_llm", side_effect=mock_call_llm):
        result = await analyze_all_checkpoints(
            SAMPLE_TRANSCRIPT, SAMPLE_CHECKPOINTS, "meaning_for_meaning"
        )

    summary = result["summary"]
    assert summary["passed"] == 1
    assert summary["failed"] == 1
    assert summary["partial"] == 1
    assert summary["score"] == "1/3"
    # 33% pass rate + all-medium-only breaches → review escalation
    # (a83e441 pass-rate guard); compliant=False.
    assert summary["bucket"] == "review"
    assert summary["compliant"] is False
    assert summary["critical_breaches"] == 0
    assert summary["high_breaches"] == 0
    assert summary["medium_breaches"] == 2  # fail + partial


@pytest.mark.asyncio
async def test_all_checkpoints_error_excluded_from_denominator():
    """Error checkpoints are excluded from the score denominator.

    Post-D02 the analyzer no longer isolates per-checkpoint errors inside a
    batch (one LLM call covers up to 6 checkpoints). To still exercise the
    "error excluded" path, the mock returns an array where the middle entry
    has status='error' inline — analyze_all_checkpoints's summary code
    excludes status='error' from `total` regardless of where it originated.
    """
    async def mock_call_llm(prompt, timeout=60.0):
        return json.dumps([
            {
                "status": "pass",
                "evidence": "we are an independent energy broker and a third party",
                "notes": None,
                "agent_name": "Sarah",
                "customer_name": "Unknown",
            },
            {
                "status": "error",
                "evidence": "API timeout",
                "notes": "Failed",
                "confidence": "low",
                "agent_name": "Unknown",
                "customer_name": "Unknown",
            },
            {
                "status": "pass",
                "evidence": "we are an independent energy broker",
                "notes": None,
                "agent_name": "Sarah",
                "customer_name": "Unknown",
            },
        ])

    with patch("app.checkpoint_analyzer._call_llm", side_effect=mock_call_llm):
        result = await analyze_all_checkpoints(
            SAMPLE_TRANSCRIPT, SAMPLE_CHECKPOINTS, "meaning_for_meaning"
        )

    assert result["summary"]["error"] == 1
    # Only 2 non-error results in denominator
    assert result["summary"]["total"] == 2
    assert result["summary"]["passed"] == 2
    assert result["summary"]["score"] == "2/2"
    assert result["summary"]["compliant"] is True


@pytest.mark.asyncio
async def test_all_checkpoints_all_error():
    """When all checkpoints error, score is 0/0 and not compliant."""

    async def mock_call_llm(prompt, timeout=60.0):
        raise Exception("All calls fail")

    with patch("app.checkpoint_analyzer._call_llm", side_effect=mock_call_llm):
        result = await analyze_all_checkpoints(
            SAMPLE_TRANSCRIPT, SAMPLE_CHECKPOINTS, "meaning_for_meaning"
        )

    assert result["summary"]["error"] == 3
    assert result["summary"]["total"] == 0
    assert result["summary"]["score"] == "0/0"
    assert result["summary"]["compliant"] is False


@pytest.mark.asyncio
async def test_all_checkpoints_empty_list():
    """Empty checkpoint list returns empty results."""
    result = await analyze_all_checkpoints(
        SAMPLE_TRANSCRIPT, [], "meaning_for_meaning"
    )

    assert len(result["results"]) == 0
    assert result["summary"]["total"] == 0
    assert result["summary"]["score"] == "0/0"
    assert result["agent_name"] == "Unknown"


@pytest.mark.asyncio
async def test_all_checkpoints_agent_name_extraction():
    """Agent and customer names are extracted from first non-Unknown result.

    Single batch of 3 → mock returns one array; the first entry has Unknown
    names so the analyzer falls through to entry 2 which carries the names.
    """
    async def mock_call_llm(prompt, timeout=60.0):
        return json.dumps([
            {
                "status": "pass",
                "evidence": "we are an independent energy broker and a third party",
                "notes": None,
                "agent_name": "Unknown",
                "customer_name": "Unknown",
            },
            {
                "status": "pass",
                "evidence": "we are not an energy supplier",
                "notes": None,
                "agent_name": "Sarah",
                "customer_name": "Mr. Johnson",
            },
            {
                "status": "pass",
                "evidence": "we are an independent energy broker",
                "notes": None,
                "agent_name": "Sarah",
                "customer_name": "Mr. Johnson",
            },
        ])

    with patch("app.checkpoint_analyzer._call_llm", side_effect=mock_call_llm):
        result = await analyze_all_checkpoints(
            SAMPLE_TRANSCRIPT, SAMPLE_CHECKPOINTS, "meaning_for_meaning"
        )

    assert result["agent_name"] == "Sarah"
    assert result["customer_name"] == "Mr. Johnson"


@pytest.mark.asyncio
async def test_analyze_enriches_results_with_start_and_end_ms(monkeypatch):
    """When word_data is provided, each result gains start_ms/end_ms from the evidence match."""

    # **_kw absorbs the W4.4/W4.7 ``similar_rejections`` kwarg the real
    # ``_analyze_batch`` now accepts — keeps this fixture forward-compatible
    # without forcing every test to pass it explicitly.
    async def fake_batch(transcript, batch, supplier, strictness, **_kw):
        return [
            {
                "section": 1,
                "name": "VAT disclosure",
                "status": "pass",
                "evidence": "the prices include VAT at the prevailing rate",
                "notes": "Agent read it verbatim.",
                "confidence": "high",
                "needs_review": False,
                "agent_name": "Sarah",
                "customer_name": "Bob",
                "verified": True,
                "similarity": 1.0,
            },
            {
                "section": 2,
                "name": "Credit-check consent",
                "status": "fail",
                "evidence": "",
                "notes": "Agent never raised credit-check consent.",
                "confidence": "high",
                "needs_review": False,
                "agent_name": "Sarah",
                "customer_name": "Bob",
                "verified": False,
                "similarity": 0.0,
            },
        ]

    monkeypatch.setattr("app.checkpoint_analyzer._analyze_batch", fake_batch)
    monkeypatch.setattr("app.checkpoint_analyzer.settings.use_agent_analyzer", False, raising=False)

    checkpoints = [
        {"section": 1, "name": "VAT disclosure", "required": "The prices include VAT.", "strictness": "verbatim", "key_phrases": []},
        {"section": 2, "name": "Credit-check consent", "required": "I need your consent.", "strictness": "mandatory", "key_phrases": []},
    ]

    # word.start/end are SECONDS (floats) per the assemblyai_transcription.py
    # convention — find_word_range multiplies by 1000 internally to return
    # the start_ms/end_ms contract. So the test fixture uses seconds.
    words = [
        {"word": "the",        "start": 42.000, "end": 42.200, "speaker": "A"},
        {"word": "prices",     "start": 42.300, "end": 42.700, "speaker": "A"},
        {"word": "include",    "start": 42.800, "end": 43.100, "speaker": "A"},
        {"word": "VAT",        "start": 43.200, "end": 43.500, "speaker": "A"},
        {"word": "at",         "start": 43.600, "end": 43.700, "speaker": "A"},
        {"word": "the",        "start": 43.800, "end": 43.900, "speaker": "A"},
        {"word": "prevailing", "start": 44.000, "end": 44.500, "speaker": "A"},
        {"word": "rate",       "start": 44.600, "end": 44.900, "speaker": "A"},
    ]

    result = await analyze_all_checkpoints(
        transcript="stub transcript",
        checkpoints=checkpoints,
        script_mode="meaning_for_meaning",
        supplier="Unknown",
        word_data=words,
    )

    assert "results" in result, f"analyzer did not return a 'results' key. Got: {list(result.keys())}"

    passed = next(r for r in result["results"] if r["name"] == "VAT disclosure")
    omitted = next(r for r in result["results"] if r["name"] == "Credit-check consent")

    assert passed["start_ms"] == 42_000, f"expected 42_000, got {passed.get('start_ms')}"
    assert passed["end_ms"] == 44_900
    assert omitted["start_ms"] is None
    assert omitted["end_ms"] is None


@pytest.mark.asyncio
async def test_analyze_without_word_data_leaves_start_and_end_ms_none(monkeypatch):
    """Legacy calls with no word_data still analyze — timestamps stay None."""

    async def fake_batch(transcript, batch, supplier, strictness, **_kw):
        return [
            {
                "section": 1,
                "name": "VAT disclosure",
                "status": "pass",
                "evidence": "the prices include VAT",
                "notes": "",
                "confidence": "high",
                "needs_review": False,
                "agent_name": "Sarah",
                "customer_name": "Bob",
                "verified": True,
                "similarity": 1.0,
            },
        ]

    monkeypatch.setattr("app.checkpoint_analyzer._analyze_batch", fake_batch)
    monkeypatch.setattr("app.checkpoint_analyzer.settings.use_agent_analyzer", False, raising=False)

    result = await analyze_all_checkpoints(
        transcript="stub",
        checkpoints=[{"section": 1, "name": "VAT disclosure", "required": "", "strictness": "verbatim", "key_phrases": []}],
        supplier="Unknown",
        script_mode="meaning_for_meaning",
        word_data=None,
    )
    assert result["results"][0]["start_ms"] is None
    assert result["results"][0]["end_ms"] is None
