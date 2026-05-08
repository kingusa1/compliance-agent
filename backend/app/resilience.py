import logging

import httpx
from tenacity import (
    retry,
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


DEEPGRAM_RETRY = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.HTTPStatusError)),
    before_sleep=_deepgram_before_sleep,
)

LLM_RETRY = retry(
    stop=stop_after_attempt(7),
    wait=wait_exponential(multiplier=2, min=5, max=90),
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.HTTPStatusError)),
    before_sleep=_llm_before_sleep,
)
