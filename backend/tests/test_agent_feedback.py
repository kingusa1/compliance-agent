import json
from unittest.mock import AsyncMock, patch

import pytest

from app.agent.feedback import abstract_and_store_review
from app.models import AgentLearning


@pytest.mark.asyncio
async def test_abstract_and_store_creates_learning(test_db):
    fake_llm_response = json.dumps({
        "pattern": "agent asked DOB without waiting for explicit yes",
        "lesson": "customer_yes checkpoints require a clear verbal yes, not trailing silence",
    })

    with patch("app.agent.feedback._call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = fake_llm_response
        await abstract_and_store_review(
            db=test_db,
            supplier="E.ON Next",
            checkpoint_name="Agent confirms DOB",
            transcript_excerpt="Agent: DOB is 14th March? Customer: (silence)",
            agent_verdict="pass",
            human_verdict="fail",
            reviewer_notes="agent rushed past without confirmation",
        )

    rows = test_db.query(AgentLearning).all()
    assert len(rows) == 1
    assert rows[0].supplier == "E.ON Next"
    assert rows[0].checkpoint_name == "Agent confirms DOB"
    assert rows[0].agent_verdict == "pass"
    assert rows[0].human_verdict == "fail"
    assert "without waiting" in rows[0].pattern
    assert "verbal yes" in rows[0].lesson


@pytest.mark.asyncio
async def test_abstract_no_store_when_agent_and_human_agree(test_db):
    """If human confirmed agent's verdict, there's no lesson to learn — don't store."""
    with patch("app.agent.feedback._call_llm", new_callable=AsyncMock) as mock_llm:
        await abstract_and_store_review(
            db=test_db,
            supplier="E.ON Next",
            checkpoint_name="CP",
            transcript_excerpt="...",
            agent_verdict="pass",
            human_verdict="pass",
            reviewer_notes=None,
        )

    assert mock_llm.await_count == 0
    assert test_db.query(AgentLearning).count() == 0


@pytest.mark.asyncio
async def test_abstract_handles_llm_failure_gracefully(test_db):
    with patch("app.agent.feedback._call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.side_effect = Exception("llm unreachable")
        await abstract_and_store_review(
            db=test_db,
            supplier="E.ON Next",
            checkpoint_name="CP",
            transcript_excerpt="agent excerpt",
            agent_verdict="pass",
            human_verdict="fail",
            reviewer_notes="wrong",
        )

    assert test_db.query(AgentLearning).count() == 0
