from unittest.mock import AsyncMock, patch

import pytest

from app.checkpoint_analyzer import analyze_all_checkpoints
from app.config import settings


TRANSCRIPT = (
    "Agent: Hi, call is recorded. "
    "Agent: Standing charge is 30 pence. "
    "Customer: Okay that's fine."
)

CHECKPOINTS = [
    {"section": 1, "name": "Recording", "required": "call recorded",
     "key_phrases": ["recorded"], "strictness": "mandatory"},
    {"section": 2, "name": "Standing charge", "required": "state standing charge",
     "key_phrases": ["standing charge"], "strictness": "mandatory"},
]


@pytest.mark.asyncio
async def test_flag_off_uses_old_batch_path(monkeypatch):
    monkeypatch.setattr(settings, "use_agent_analyzer", False)
    old_return = [
        {"section": 1, "name": "Recording", "status": "pass", "confidence": "high",
         "evidence": "recorded", "notes": None, "needs_review": False,
         "verified": True, "similarity": 1.0, "agent_name": "A", "customer_name": "C"},
        {"section": 2, "name": "Standing charge", "status": "pass", "confidence": "high",
         "evidence": "30 pence", "notes": None, "needs_review": False,
         "verified": True, "similarity": 1.0, "agent_name": "A", "customer_name": "C"},
    ]
    with patch("app.checkpoint_analyzer._analyze_batch", new_callable=AsyncMock) as old_path:
        old_path.return_value = old_return
        with patch("app.checkpoint_analyzer.run_batch_tiered", new_callable=AsyncMock) as agent_path:
            result = await analyze_all_checkpoints(TRANSCRIPT, CHECKPOINTS, "meaning_for_meaning", "E.ON Next")

    old_path.assert_awaited()
    agent_path.assert_not_called()
    assert result["summary"]["compliant"] is True


@pytest.mark.asyncio
async def test_flag_on_uses_agent_path(monkeypatch):
    monkeypatch.setattr(settings, "use_agent_analyzer", True)
    agent_return = [
        {"section": 1, "name": "Recording", "status": "pass", "confidence": "high",
         "evidence": "recorded", "notes": None, "needs_review": False, "escalated": False,
         "verified": True, "similarity": 1.0, "agent_name": "A", "customer_name": "C"},
        {"section": 2, "name": "Standing charge", "status": "pass", "confidence": "high",
         "evidence": "30 pence", "notes": None, "needs_review": False, "escalated": False,
         "verified": True, "similarity": 1.0, "agent_name": "A", "customer_name": "C"},
    ]
    with patch("app.checkpoint_analyzer._analyze_batch", new_callable=AsyncMock) as old_path:
        with patch("app.checkpoint_analyzer.run_batch_tiered", new_callable=AsyncMock) as agent_path:
            agent_path.return_value = agent_return
            result = await analyze_all_checkpoints(TRANSCRIPT, CHECKPOINTS, "meaning_for_meaning", "E.ON Next")

    agent_path.assert_awaited()
    old_path.assert_not_called()
    assert result["summary"]["compliant"] is True
