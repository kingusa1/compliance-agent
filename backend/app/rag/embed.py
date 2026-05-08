"""OpenAI text-embedding-3-small client for L6 RAG ingestion.

Lazy singleton (no API key needed at import time). `embed_one` returns a
1536-dim vector; `embed_batch` slices into batches of 100 and retries on
RateLimitError up to 3 attempts with exponential backoff (0.5s, 1s, 2s).

If OPENAI_API_KEY is unset, `_get_client()` raises EnvironmentError so
callers can decide to skip ingestion (graceful_degrade per L6 design).
"""
from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger(__name__)

def _embed_model() -> str:
    """OpenRouter expects 'openai/text-embedding-3-small'; OpenAI direct
    expects 'text-embedding-3-small'. Pick by which key is present."""
    if os.getenv("OPENROUTER_API_KEY", "").strip():
        return "openai/text-embedding-3-small"
    return "text-embedding-3-small"


EMBED_MODEL = "text-embedding-3-small"  # fallback constant; runtime uses _embed_model()
BATCH_SIZE = 100
MAX_RETRIES = 3

_client = None


def _get_client():
    """Lazy OpenAI-compat client. Routes via OpenRouter by default.

    OpenRouter exposes openai/text-embedding-3-small via the same
    /embeddings REST shape as OpenAI direct, so the openai SDK works
    unchanged with base_url override. Falls back to OpenAI direct only
    if OPENROUTER_API_KEY missing AND OPENAI_API_KEY present.

    Loads .env at first use because Inngest worker processes don't
    inherit the FastAPI dotenv chain — pydantic-settings only loads
    .env into Settings, not into os.environ.
    """
    global _client
    if _client is not None:
        return _client
    try:
        from dotenv import load_dotenv
        load_dotenv()  # idempotent
    except ImportError:
        pass

    or_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    oa_key = os.getenv("OPENAI_API_KEY", "").strip()

    from openai import OpenAI  # lazy import — keeps module importable in tests

    if or_key:
        _client = OpenAI(
            api_key=or_key,
            base_url="https://openrouter.ai/api/v1",
        )
        logger.info("RAG embed: using OpenRouter (openai/text-embedding-3-small)")
    elif oa_key:
        _client = OpenAI(api_key=oa_key)
        logger.info("RAG embed: using OpenAI direct")
    else:
        raise EnvironmentError(
            "Neither OPENROUTER_API_KEY nor OPENAI_API_KEY set — RAG embeddings unavailable"
        )
    return _client


def embed_one(text: str) -> list[float]:
    """Return a 1536-dim embedding for a single text. Raises on API failure."""
    client = _get_client()
    r = client.embeddings.create(model=_embed_model(), input=[text[:8000]])
    return r.data[0].embedding


def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed `texts` in batches of 100. Retries on RateLimitError up to 3 attempts.

    Returns a list of vectors aligned with input order. Raises EnvironmentError
    if OPENAI_API_KEY is unset; the caller decides whether to skip or surface.
    """
    if not texts:
        return []
    client = _get_client()

    # Lazy import to find the RateLimitError class without forcing openai at import time.
    try:
        from openai import RateLimitError  # type: ignore
    except Exception:  # pragma: no cover
        RateLimitError = Exception  # type: ignore

    out: list[list[float]] = []
    for start in range(0, len(texts), BATCH_SIZE):
        chunk = [t[:8000] for t in texts[start : start + BATCH_SIZE]]
        attempt = 0
        while True:
            try:
                r = client.embeddings.create(model=_embed_model(), input=chunk)
                out.extend(d.embedding for d in r.data)
                break
            except RateLimitError as e:
                attempt += 1
                if attempt >= MAX_RETRIES:
                    logger.warning("embed_batch rate limit exhausted after %d attempts", attempt)
                    raise
                backoff = 0.5 * (2 ** (attempt - 1))
                logger.warning("embed_batch rate limited, retrying in %.1fs (attempt %d)", backoff, attempt)
                time.sleep(backoff)
            except Exception:
                # Non-rate-limit failures propagate immediately — caller decides.
                raise
    return out
