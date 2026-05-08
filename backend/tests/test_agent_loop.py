import json
from unittest.mock import AsyncMock, patch

import pytest

from app.agent.agent_loop import run_agent_on_batch
from app.agent.tool_handlers import ToolContext


SAMPLE_TRANSCRIPT = (
    "Agent: Hi, I'm Alex from What Utilities. This call is recorded for compliance. "
    "Customer: Okay, that's fine. "
    "Agent: Your standing charge will be 30 pence per day."
)

BATCH_CHECKPOINTS = [
    {"section": 1, "name": "Agent mentions call recording",
     "required": "Agent states call is recorded",
     "key_phrases": ["recorded", "taped"],
     "strictness": "mandatory"},
    {"section": 2, "name": "Standing charge disclosure",
     "required": "Agent states the standing charge in pence per day",
     "key_phrases": ["standing charge", "pence"],
     "strictness": "mandatory"},
]


def _final_message_response(verdicts: list[dict]) -> dict:
    """Simulate OpenRouter returning a final assistant message with JSON verdicts."""
    return {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": json.dumps(verdicts),
                "tool_calls": None,
            },
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50},
    }


def _tool_call_response(tool_calls: list[dict]) -> dict:
    """Simulate OpenRouter returning a message with tool calls to execute."""
    return {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": tool_calls,
            },
            "finish_reason": "tool_calls",
        }],
    }


def _ctx():
    return ToolContext(
        transcript=SAMPLE_TRANSCRIPT,
        word_data=[],
        supplier="E.ON Next",
        agent_speaker_label="A",
        customer_speaker_label="B",
        db=None,
    )


@pytest.mark.asyncio
async def test_agent_loop_single_turn_with_direct_verdicts():
    """LLM returns verdicts immediately, no tool calls needed."""
    direct_verdicts = [
        {"name": "Agent mentions call recording", "status": "pass", "confidence": "high",
         "evidence": "This call is recorded for compliance", "notes": None},
        {"name": "Standing charge disclosure", "status": "pass", "confidence": "high",
         "evidence": "Your standing charge will be 30 pence per day", "notes": None},
    ]

    with patch("app.agent.agent_loop._call_llm_with_tools", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = _final_message_response(direct_verdicts)
        results = await run_agent_on_batch(
            _ctx(), BATCH_CHECKPOINTS, model="google/gemini-2.5-flash",
        )

    assert len(results) == 2
    assert results[0]["name"] == "Agent mentions call recording"
    assert results[0]["status"] == "pass"
    assert results[0]["confidence"] == "high"
    assert mock_llm.await_count == 1


@pytest.mark.asyncio
async def test_agent_loop_tool_call_then_verdict():
    """LLM first asks for a tool, then returns verdicts."""
    tool_call = {
        "id": "call_1",
        "type": "function",
        "function": {
            "name": "find_evidence",
            "arguments": json.dumps({"query": "30 pence per day"}),
        },
    }
    direct_verdicts = [
        {"name": "Agent mentions call recording", "status": "pass", "confidence": "high",
         "evidence": "This call is recorded", "notes": None},
        {"name": "Standing charge disclosure", "status": "pass", "confidence": "high",
         "evidence": "standing charge will be 30 pence per day", "notes": None},
    ]

    responses = [
        _tool_call_response([tool_call]),
        _final_message_response(direct_verdicts),
    ]

    with patch("app.agent.agent_loop._call_llm_with_tools", new_callable=AsyncMock) as mock_llm:
        mock_llm.side_effect = responses
        results = await run_agent_on_batch(
            _ctx(), BATCH_CHECKPOINTS, model="google/gemini-2.5-flash",
        )

    assert len(results) == 2
    assert mock_llm.await_count == 2


@pytest.mark.asyncio
async def test_agent_loop_respects_max_turns():
    """If LLM keeps asking for tools, we stop after max_turns and return error results."""
    tool_call = {
        "id": "call_x",
        "type": "function",
        "function": {"name": "find_evidence", "arguments": json.dumps({"query": "x"})},
    }
    # Always return tool calls, never a final message
    forever_tool_response = _tool_call_response([tool_call])

    with patch("app.agent.agent_loop._call_llm_with_tools", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = forever_tool_response
        results = await run_agent_on_batch(
            _ctx(), BATCH_CHECKPOINTS, model="google/gemini-2.5-flash",
            max_turns=3,
        )

    assert all(r["status"] == "error" for r in results)
    assert all(r["needs_review"] is True for r in results)
    assert mock_llm.await_count == 3


@pytest.mark.asyncio
async def test_agent_loop_marks_low_confidence_as_needs_review():
    verdicts = [
        {"name": "Agent mentions call recording", "status": "pass", "confidence": "low",
         "evidence": "recorded", "notes": "agent was mumbling"},
        {"name": "Standing charge disclosure", "status": "pass", "confidence": "high",
         "evidence": "30 pence per day", "notes": None},
    ]
    with patch("app.agent.agent_loop._call_llm_with_tools", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = _final_message_response(verdicts)
        results = await run_agent_on_batch(
            _ctx(), BATCH_CHECKPOINTS, model="google/gemini-2.5-flash",
        )

    assert results[0]["needs_review"] is True
    assert results[1]["needs_review"] is False


@pytest.mark.asyncio
async def test_agent_loop_handles_empty_choices():
    """OpenRouter returns error/rate-limited response with no choices."""
    empty_response = {"choices": [], "error": "rate limited"}
    with patch("app.agent.agent_loop._call_llm_with_tools", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = empty_response
        results = await run_agent_on_batch(
            _ctx(), BATCH_CHECKPOINTS, model="google/gemini-2.5-flash",
        )
    assert all(r["status"] == "error" for r in results)
    assert all(r["needs_review"] is True for r in results)


@pytest.mark.asyncio
async def test_agent_loop_handles_llm_exception():
    """_call_llm_with_tools raises (network error, etc.) — returns error results."""
    with patch("app.agent.agent_loop._call_llm_with_tools", new_callable=AsyncMock) as mock_llm:
        mock_llm.side_effect = Exception("network timeout")
        results = await run_agent_on_batch(
            _ctx(), BATCH_CHECKPOINTS, model="google/gemini-2.5-flash",
        )
    assert all(r["status"] == "error" for r in results)
    assert "network timeout" in results[0]["notes"]


def test_parse_verdicts_handles_fence_without_newline():
    """```json{...}``` with no newline after opening fence."""
    from app.agent.agent_loop import _parse_verdicts
    # Both formats should parse
    assert _parse_verdicts('```json\n[{"name":"x","status":"pass"}]\n```') is not None
    assert _parse_verdicts('```json[{"name":"x","status":"pass"}]```') is not None
    # Malformed is handled gracefully (returns None, not raise)
    assert _parse_verdicts('```not json at all```') is None
    assert _parse_verdicts('') is None
