"""Regression coverage for PII token sanitization (2026-05-18 audit).

The deepgram/assemblyai PII redactors emit bracketed markers like
``[PERSON_NAME]`` / ``[date_1]`` / ``[PHONE_NUMBER]``. Both the regex layer
and the LLM occasionally captured those tokens verbatim, polluting
``Call.customer_name`` / ``Call.agent_name`` / ``CustomerDeal.customer_name``
with literal ``"[PERSON_NAME]"`` strings (Crosby Grange lead-gen call).

These tests assert the sanitizer collapses pure tokens to "Unknown",
strips embedded tokens, and is wired into both ``detect_names`` and
``detect_business_name``.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.analysis import _PII_TOKEN_RE, _strip_pii_tokens, detect_names
from app.business_detect import detect_business_name


def test_strip_pii_token_pure_token_collapses_to_unknown() -> None:
    assert _strip_pii_tokens("[PERSON_NAME]") == "Unknown"
    assert _strip_pii_tokens("[date_1]") == "Unknown"
    assert _strip_pii_tokens("[PHONE_NUMBER]") == "Unknown"


def test_strip_pii_token_embedded_token_is_stripped() -> None:
    # "[PERSON_NAME] Doe" → "Doe"
    assert _strip_pii_tokens("[PERSON_NAME] Doe") == "Doe"
    # "Crosby [date_1]" → "Crosby"
    assert _strip_pii_tokens("Crosby [date_1]") == "Crosby"
    # Spaces collapse cleanly even with trailing punctuation.
    assert _strip_pii_tokens("[PERSON_NAME], CEO") == "CEO"


def test_strip_pii_token_real_name_passes_through() -> None:
    assert _strip_pii_tokens("Tom Kelly") == "Tom Kelly"
    assert _strip_pii_tokens("Awais Mustafa") == "Awais Mustafa"


def test_strip_pii_token_handles_falsy_input() -> None:
    assert _strip_pii_tokens(None) == "Unknown"
    assert _strip_pii_tokens("") == "Unknown"
    assert _strip_pii_tokens("   ") == "Unknown"


def test_pii_token_regex_matches_expected_shapes() -> None:
    assert _PII_TOKEN_RE.fullmatch("[PERSON_NAME]")
    assert _PII_TOKEN_RE.fullmatch("[date_1]")
    assert _PII_TOKEN_RE.fullmatch("[PHONE_NUMBER]")
    # Brackets with numbers only should NOT match — those aren't PII tokens.
    assert _PII_TOKEN_RE.fullmatch("[123]") is None
    # Plain names should not match.
    assert _PII_TOKEN_RE.fullmatch("Tom Kelly") is None


def _mock_llm_response(payload_text: str) -> MagicMock:
    """Build the AsyncClient.post() response shape expected by ``_call_llm``."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": payload_text}}]
    }
    mock_response.raise_for_status = MagicMock()
    return mock_response


def _patch_llm(mock_response: MagicMock):
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return patch("app.analysis.httpx.AsyncClient", return_value=mock_client)


@pytest.mark.asyncio
async def test_detect_names_strips_pii_token_from_customer() -> None:
    # LLM returns "[PERSON_NAME]" as the customer (the Crosby Grange bug).
    payload = "AGENT: Tom Kelly\nCUSTOMER: [PERSON_NAME]"
    with _patch_llm(_mock_llm_response(payload)):
        agent, customer = await detect_names("Hello, my name is Tom Kelly...")
    assert agent == "Tom Kelly"
    assert customer == "Unknown"


@pytest.mark.asyncio
async def test_detect_names_strips_pii_token_from_agent() -> None:
    payload = "AGENT: [PERSON_NAME]\nCUSTOMER: Awais Mustafa"
    with _patch_llm(_mock_llm_response(payload)):
        agent, customer = await detect_names(
            "transcript with no regex-detectable agent self-intro"
        )
    assert agent == "Unknown"
    assert customer == "Awais Mustafa"


@pytest.mark.asyncio
async def test_detect_business_name_strips_pii_token() -> None:
    """LLM returns a bracketed PII token; sanitizer should drop it."""
    with _patch_llm(_mock_llm_response("[PERSON_NAME]")):
        name = await detect_business_name("transcript body")
    assert name is None


@pytest.mark.asyncio
async def test_detect_business_name_keeps_real_company() -> None:
    with _patch_llm(_mock_llm_response("Crosby Grange Properties Limited")):
        name = await detect_business_name("transcript body")
    assert name == "Crosby Grange Properties Limited"
