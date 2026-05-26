"""Tests for retry logic in resilience.py, analysis.py, and transcription.py."""
import json
from unittest.mock import AsyncMock, MagicMock, patch, call

import httpx
import pytest
from tenacity import RetryError

from app.resilience import DEEPGRAM_RETRY, LLM_RETRY, LLMResponseError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ok_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"choices": [{"message": {"content": json.dumps(payload)}}]}
    resp.raise_for_status = MagicMock()
    return resp


def _make_error_response(status_code: int = 500) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    request = MagicMock()
    resp.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError(
            f"{status_code} Server Error", request=request, response=resp
        )
    )
    return resp


def _patch_llm_client(responses):
    """Patch httpx.AsyncClient so successive .post() calls return items from *responses*."""
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=responses)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return patch("app.analysis.httpx.AsyncClient", return_value=mock_client), mock_client


# ---------------------------------------------------------------------------
# LLM_RETRY — unit tests on the decorator itself
# ---------------------------------------------------------------------------

class TestOpenRouterRetry:
    @pytest.mark.asyncio
    async def test_succeeds_on_first_attempt(self):
        call_count = 0

        @LLM_RETRY
        async def fn():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = await fn()
        assert result == "ok"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retries_on_timeout_then_succeeds(self):
        call_count = 0

        @LLM_RETRY
        async def fn():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise httpx.TimeoutException("timeout")
            return "ok"

        result = await fn()
        assert result == "ok"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_retries_on_http_status_error_then_succeeds(self):
        call_count = 0

        @LLM_RETRY
        async def fn():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise httpx.HTTPStatusError(
                    "500", request=MagicMock(), response=MagicMock()
                )
            return "ok"

        result = await fn()
        assert result == "ok"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_exhausts_retries_and_raises(self):
        """LLM_RETRY allows 7 attempts total; after that raises RetryError."""
        call_count = 0

        @LLM_RETRY
        async def fn():
            nonlocal call_count
            call_count += 1
            raise httpx.TimeoutException("timeout")

        with pytest.raises(RetryError):
            await fn()

        assert call_count == 7

    @pytest.mark.asyncio
    async def test_does_not_retry_non_retriable_exception(self):
        call_count = 0

        @LLM_RETRY
        async def fn():
            nonlocal call_count
            call_count += 1
            raise ValueError("not retriable")

        with pytest.raises(ValueError):
            await fn()

        assert call_count == 1

    # ----- Wave 11 (2026-05-28) regression coverage --------------------
    # `LLMResponseError(RuntimeError)` is raised by analysis.py when the
    # provider returns HTTP 200 with a malformed/error envelope (rate-
    # limit, overloaded, partial-failure). It MUST be retried — the bug
    # python-reviewer + security-reviewer both caught pre-push was that
    # the first wave 11 draft used bare RuntimeError, which is NOT in
    # `_llm_should_retry`, so a transient overloaded blip would hard-fail
    # the pipeline. These tests lock the retry contract.

    @pytest.mark.asyncio
    async def test_retries_on_llm_response_error_then_succeeds(self):
        """LLMResponseError is retriable (wave 11 contract)."""
        call_count = 0

        @LLM_RETRY
        async def fn():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise LLMResponseError("OpenRouter returned no choices (type=rate_limit code=429)")
            return "ok"

        result = await fn()
        assert result == "ok"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_exhausts_retries_on_persistent_llm_response_error(self):
        """LLMResponseError uses all 7 attempts; persistent failure raises RetryError."""
        call_count = 0

        @LLM_RETRY
        async def fn():
            nonlocal call_count
            call_count += 1
            raise LLMResponseError("Anthropic returned no content (type=overloaded_error code=529)")

        with pytest.raises(RetryError):
            await fn()

        assert call_count == 7

    def test_llm_response_error_is_runtime_error_subclass(self):
        """Existing code paths that broadly `except RuntimeError` (the
        legacy contract before the typed subclass) still catch the new
        envelope errors. Locks the inheritance so future refactors don't
        silently break that compatibility."""
        assert issubclass(LLMResponseError, RuntimeError)
        try:
            raise LLMResponseError("x")
        except RuntimeError:
            pass


# ---------------------------------------------------------------------------
# DEEPGRAM_RETRY — unit tests on the decorator itself
# ---------------------------------------------------------------------------

class TestDeepgramRetry:
    @pytest.mark.asyncio
    async def test_succeeds_on_first_attempt(self):
        call_count = 0

        @DEEPGRAM_RETRY
        async def fn():
            nonlocal call_count
            call_count += 1
            return "transcript"

        result = await fn()
        assert result == "transcript"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retries_up_to_3_times_on_timeout(self):
        """DEEPGRAM_RETRY allows 3 attempts; all fail → RetryError."""
        call_count = 0

        @DEEPGRAM_RETRY
        async def fn():
            nonlocal call_count
            call_count += 1
            raise httpx.TimeoutException("timeout")

        with pytest.raises(RetryError):
            await fn()

        assert call_count == 3

    @pytest.mark.asyncio
    async def test_retries_on_timeout_then_succeeds(self):
        call_count = 0

        @DEEPGRAM_RETRY
        async def fn():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise httpx.TimeoutException("timeout")
            return "ok"

        result = await fn()
        assert result == "ok"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_does_not_retry_non_retriable_exception(self):
        call_count = 0

        @DEEPGRAM_RETRY
        async def fn():
            nonlocal call_count
            call_count += 1
            raise RuntimeError("deepgram internal error")

        with pytest.raises(RuntimeError):
            await fn()

        assert call_count == 1


# ---------------------------------------------------------------------------
# Integration: _call_llm retries on HTTP errors
# ---------------------------------------------------------------------------

class TestCallLlmRetry:
    @pytest.mark.asyncio
    async def test_call_llm_retries_on_http_error_then_succeeds(self):
        """_call_llm should retry once on HTTPStatusError and succeed on 2nd attempt."""
        from app.analysis import _call_llm

        # content is the raw string returned by the LLM (no JSON wrapping here)
        payload = {"choices": [{"message": {"content": "hello"}}]}

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.json.return_value = payload
        ok_resp.raise_for_status = MagicMock()

        err_resp = _make_error_response(500)

        patcher, mock_client = _patch_llm_client([err_resp, ok_resp])
        with patcher:
            result = await _call_llm("test prompt")
        assert result == "hello"
        assert mock_client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_call_llm_raises_after_exhausting_retries(self):
        """_call_llm raises RetryError after 7 consecutive HTTPStatusError responses
        (LLM_RETRY uses stop_after_attempt(7))."""
        from app.analysis import _call_llm

        # Need 7 responses since LLM_RETRY allows 7 attempts before giving up.
        err_responses = [_make_error_response(503) for _ in range(7)]

        patcher, mock_client = _patch_llm_client(err_responses)
        with patcher:
            with pytest.raises(RetryError):
                await _call_llm("test prompt")

        assert mock_client.post.call_count == 7


# ---------------------------------------------------------------------------
# Integration: _call_deepgram retries
# ---------------------------------------------------------------------------

def _import_call_deepgram():
    """Import _call_deepgram from the transcription module."""
    from app.transcription import _call_deepgram  # noqa: PLC0415
    return _call_deepgram


class TestCallDeepgramRetry:
    @pytest.mark.asyncio
    async def test_call_deepgram_retries_on_timeout_then_succeeds(self):
        """_call_deepgram retries up to 3 times; succeeds on 3rd attempt."""
        _call_deepgram = _import_call_deepgram()

        call_count = 0
        mock_response = MagicMock()
        mock_client = MagicMock()

        async def fake_transcribe(source, options):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise httpx.TimeoutException("timeout")
            return mock_response

        mock_client.listen.asyncrest.v.return_value.transcribe_file = fake_transcribe

        result = await _call_deepgram(mock_client, {"buffer": b""}, MagicMock())
        assert result is mock_response
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_call_deepgram_raises_after_3_failures(self):
        """_call_deepgram raises RetryError after 3 consecutive timeouts."""
        _call_deepgram = _import_call_deepgram()

        call_count = 0
        mock_client = MagicMock()

        async def fake_transcribe(source, options):
            nonlocal call_count
            call_count += 1
            raise httpx.TimeoutException("timeout")

        mock_client.listen.asyncrest.v.return_value.transcribe_file = fake_transcribe

        with pytest.raises(RetryError):
            await _call_deepgram(mock_client, {"buffer": b""}, MagicMock())

        assert call_count == 3
