import json
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from app.analysis import analyze_compliance_v1
from app.schemas import ComplianceResult


COMPLIANT_TRANSCRIPT = """Agent: Good morning, my name is Sarah calling from Energy Solutions. I want to be upfront with you - we are an independent energy broker and a third party. We are not an energy supplier like British Gas or E.ON Next. We compare deals across multiple suppliers to find you the best rate.
Customer: Oh okay, so you're not from British Gas then?
Agent: No, not at all. We are a completely separate company. We act as a broker to help you find the best energy deal."""

NON_COMPLIANT_TRANSCRIPT = """Agent: Hi there, I'm calling from Energy Solutions about your energy tariff. We can save you money on your bills.
Customer: Are you from British Gas?
Agent: We work with all the major suppliers to find you the best deal. Let me just pull up your account details."""


def _mock_llm_response(payload: dict) -> MagicMock:
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": json.dumps(payload)}}]
    }
    mock_response.raise_for_status = MagicMock()
    return mock_response


def _patch_llm(mock_response):
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return patch("app.analysis.httpx.AsyncClient", return_value=mock_client)


@pytest.mark.asyncio
async def test_analyze_compliant_call_with_checkpoints():
    payload = {
        "compliant": True,
        "reason": "Agent clearly stated they are an independent energy broker and third party.",
        "excerpt": "we are an independent energy broker and a third party",
        "agent_name": "Sarah",
        "customer_name": "Unknown",
        "checkpoints": [
            {"rule": "The agent explicitly states the company is a third party", "passed": True,
             "excerpt": "we are an independent energy broker and a third party"},
            {"rule": "The agent states the company is NOT an energy supplier", "passed": True,
             "excerpt": "We are not an energy supplier like British Gas or E.ON Next"},
            {"rule": "The agent identifies themselves/company as an independent broker or intermediary", "passed": True,
             "excerpt": "We act as a broker to help you find the best energy deal"},
        ],
    }
    mock_response = _mock_llm_response(payload)

    with _patch_llm(mock_response):
        result = await analyze_compliance_v1(COMPLIANT_TRANSCRIPT)
        assert isinstance(result, ComplianceResult)
        assert result.compliant is True
        assert len(result.checkpoints) == 3
        assert all(cp.passed for cp in result.checkpoints)
        assert result.checkpoints[0].rule == "The agent explicitly states the company is a third party"
        assert "third party" in result.checkpoints[0].excerpt


@pytest.mark.asyncio
async def test_analyze_non_compliant_call_with_checkpoints():
    payload = {
        "compliant": False,
        "reason": "Agent never identified the company as a third-party broker.",
        "excerpt": "We work with all the major suppliers to find you the best deal",
        "agent_name": "Unknown",
        "customer_name": "Unknown",
        "checkpoints": [
            {"rule": "The agent explicitly states the company is a third party", "passed": False,
             "excerpt": "We work with all the major suppliers to find you the best deal"},
            {"rule": "The agent states the company is NOT an energy supplier", "passed": False,
             "excerpt": "We work with all the major suppliers to find you the best deal"},
            {"rule": "The agent identifies themselves/company as an independent broker or intermediary", "passed": False,
             "excerpt": "We work with all the major suppliers to find you the best deal"},
        ],
    }
    mock_response = _mock_llm_response(payload)

    with _patch_llm(mock_response):
        result = await analyze_compliance_v1(NON_COMPLIANT_TRANSCRIPT)
        assert isinstance(result, ComplianceResult)
        assert result.compliant is False
        assert len(result.checkpoints) == 3
        assert not any(cp.passed for cp in result.checkpoints)


@pytest.mark.asyncio
async def test_analyze_handles_markdown_code_blocks():
    """Test that analysis handles Claude wrapping JSON in markdown code blocks."""
    payload = {
        "compliant": True,
        "reason": "Good",
        "excerpt": "We are a broker",
        "agent_name": "Unknown",
        "customer_name": "Unknown",
        "checkpoints": [
            {"rule": "The agent explicitly states the company is a third party", "passed": True, "excerpt": "We are a broker"},
            {"rule": "The agent states the company is NOT an energy supplier", "passed": True, "excerpt": "We are a broker"},
            {"rule": "The agent identifies themselves/company as an independent broker", "passed": True, "excerpt": "We are a broker"},
        ],
    }
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "```json\n" + json.dumps(payload) + "\n```"}}]
    }
    mock_response.raise_for_status = MagicMock()

    with _patch_llm(mock_response):
        result = await analyze_compliance_v1("test transcript")
        assert result.compliant is True
        assert len(result.checkpoints) == 3


@pytest.mark.asyncio
async def test_analyze_backward_compat_no_checkpoints():
    """Test that V1 works if LLM returns no checkpoints (backward compat)."""
    payload = {
        "compliant": True,
        "reason": "Good",
        "excerpt": "We are a broker",
        "agent_name": "Unknown",
        "customer_name": "Unknown",
    }
    mock_response = _mock_llm_response(payload)

    with _patch_llm(mock_response):
        result = await analyze_compliance_v1("test transcript")
        assert result.compliant is True
        assert result.checkpoints == []
