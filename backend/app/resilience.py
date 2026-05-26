import logging

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)


def _deepgram_before_sleep(retry_state):
    logger.warning(
        "Deepgram retry %d: %s",
        retry_state.attempt_number,
        retry_state.outcome.exception(),
    )


def _llm_before_sleep(retry_state):
    logger.warning(
        "LLM retry %d: %s",
        retry_state.attempt_number,
        retry_state.outcome.exception(),
    )


# Permanent failures — don't retry. 402=billing, 401=bad key,
# 403=key disabled, 404=wrong model id. 429 is throttling, DO retry.
_LLM_PERMANENT_4XX = {400, 401, 402, 403, 404}


class LLMResponseError(RuntimeError):
    """LLM provider returned HTTP 200 with a malformed/error body.

    OpenRouter and Anthropic both return rate-limit / overloaded / partial-
    failure envelopes as HTTP 200 bodies with an `error` field instead of
    proper 4xx/5xx codes. Wave 11 (2026-05-28) raises this for the missing-
    `choices` / missing-`content` defensive-shape path. Listed in
    `_llm_should_retry` so tenacity treats the transient envelope the same
    as a 5xx — retry up to 7 times — instead of hard-failing on attempt 1.
    """


def _llm_should_retry(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, LLMResponseError):
        return True  # 200-with-error-envelope from OpenRouter/Anthropic
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in _LLM_PERMANENT_4XX:
            return False  # permanent — no point retrying
        return True
    return False


DEEPGRAM_RETRY = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.HTTPStatusError)),
    before_sleep=_deepgram_before_sleep,
)

LLM_RETRY = retry(
    stop=stop_after_attempt(7),
    wait=wait_exponential(multiplier=2, min=5, max=90),
    # Skip retry on permanent 4xx (402 Payment Required, 401 bad key,
    # 403 disabled, 404 wrong model). Retrying those wastes ~3 min and
    # never succeeds without a billing/key change.
    retry=retry_if_exception(_llm_should_retry),
    before_sleep=_llm_before_sleep,
)
