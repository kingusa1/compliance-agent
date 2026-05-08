from unittest.mock import AsyncMock, patch

import pytest

from app.agent.escalation import run_batch_tiered
from app.agent.tool_handlers import ToolContext


BATCH = [
    {"section": 1, "name": "CP1", "strictness": "mandatory", "required": "x", "key_phrases": []},
    {"section": 2, "name": "CP2", "strictness": "mandatory", "required": "y", "key_phrases": []},
]


def _ctx():
    return ToolContext(
        transcript="dummy transcript",
        word_data=[],
        supplier="E.ON Next",
        agent_speaker_label="A",
        customer_speaker_label="B",
        db=None,
    )


@pytest.mark.asyncio
async def test_all_high_confidence_no_escalation():
    first_pass = [
        {"section": 1, "name": "CP1", "status": "pass", "confidence": "high",
         "evidence": "e1", "notes": None, "needs_review": False,
         "verified": True, "similarity": 1.0, "agent_name": "A", "customer_name": "C"},
        {"section": 2, "name": "CP2", "status": "fail", "confidence": "high",
         "evidence": "NOT FOUND IN TRANSCRIPT", "notes": None, "needs_review": False,
         "verified": True, "similarity": 1.0, "agent_name": "A", "customer_name": "C"},
    ]
    with patch("app.agent.escalation.run_agent_on_batch", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = first_pass
        results = await run_batch_tiered(_ctx(), BATCH)

    assert len(results) == 2
    assert mock_run.await_count == 1  # only first pass ran
    assert all(r["escalated"] is False for r in results)


@pytest.mark.asyncio
async def test_low_confidence_triggers_escalation():
    first_pass = [
        {"section": 1, "name": "CP1", "status": "pass", "confidence": "low",
         "evidence": "maybe", "notes": None, "needs_review": True,
         "verified": True, "similarity": 0.8, "agent_name": "A", "customer_name": "C"},
        {"section": 2, "name": "CP2", "status": "pass", "confidence": "high",
         "evidence": "clear", "notes": None, "needs_review": False,
         "verified": True, "similarity": 1.0, "agent_name": "A", "customer_name": "C"},
    ]
    escalated_pass = [
        {"section": 1, "name": "CP1", "status": "fail", "confidence": "high",
         "evidence": "NOT FOUND IN TRANSCRIPT", "notes": "on second look, not really",
         "needs_review": False, "verified": True, "similarity": 1.0,
         "agent_name": "A", "customer_name": "C"},
    ]
    with patch("app.agent.escalation.run_agent_on_batch", new_callable=AsyncMock) as mock_run:
        mock_run.side_effect = [first_pass, escalated_pass]
        results = await run_batch_tiered(_ctx(), BATCH)

    assert mock_run.await_count == 2
    # Only CP1 was re-run
    assert mock_run.await_args_list[1].args[1] == [BATCH[0]]
    # CP1 is now escalated and updated
    cp1 = next(r for r in results if r["name"] == "CP1")
    assert cp1["status"] == "fail"
    assert cp1["escalated"] is True
    # CP2 passed through unchanged
    cp2 = next(r for r in results if r["name"] == "CP2")
    assert cp2["status"] == "pass"
    assert cp2["escalated"] is False
