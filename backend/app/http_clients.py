"""Shared httpx.AsyncClient factory + provider-level concurrency throttles.

2026-05-26 enterprise wave (citations in BRAIN/04_Sessions/2026-05-26_…).

Why this exists
---------------
The previous pattern was `async with httpx.AsyncClient() as client:` inside
every `_call_*` function in `app/analysis.py`. That allocated a fresh
connection pool per call, defeated TCP keep-alive entirely (every call paid
the TLS handshake), and exposed two production failures observed
2026-05-25 19:02 UTC:

1. **httpcore `_state_lock` race.** With many in-flight cancellations,
   httpcore's `aclose()` path acquires `_state_lock` while a cancellation
   is mid-flight. Documented in encode/httpcore #783 and #395.
2. **No upstream-rate throttle.** A single phrase-pack analysis fanned
   out 15 concurrent LLM POSTs; 5 simultaneous pipelines stacked to 75
   in-flight, exceeding httpx's default pool ceiling (100/20) and
   OpenRouter+Anthropic's per-account concurrency tolerance.

This module fixes both at the boundary every LLM call goes through.

Posture mirrors the anthropic-sdk-python defaults
(`DEFAULT_CONNECTION_LIMITS = Limits(1000, 100)` — too aggressive for
our compute; we use 200/100), the openai-python `DefaultAsyncHttpxClient`,
and the patterns documented at:

  * https://www.python-httpx.org/advanced/resource-limits/
  * https://github.com/anthropics/anthropic-sdk-python/blob/main/src/anthropic/_constants.py
  * https://github.com/encode/httpcore/discussions/783

Public surface
--------------
- ``get_async_client()`` — per-event-loop singleton ``httpx.AsyncClient``.
- ``openrouter_semaphore()`` — process-wide ``asyncio.Semaphore(24)``
  gating every OpenRouter POST. Sized to upstream tolerance, NOT
  to our fanout dream (75 is wrong; queue at the boundary).
- ``anthropic_semaphore()`` — process-wide ``asyncio.Semaphore(20)``
  for direct Anthropic calls (when ``ACTIVE_PROVIDER=anthropic``).
- ``aclose_all_clients()`` — call from FastAPI lifespan shutdown so the
  pool drains before the worker exits.
"""
from __future__ import annotations

import asyncio
import logging
import weakref
from typing import Optional

import httpx

log = logging.getLogger(__name__)

# ── Tuning constants (one place to change pool size) ──────────────────────
# Pool ≈ 2.5× peak fanout so cancellations + retries have headroom.
# Anthropic SDK uses 1000/100 by default; OpenAI mirrors it. We chose
# 200/100 to fit Railway Pro per-replica file-descriptor limits.
_LIMITS = httpx.Limits(
    max_connections=200,
    max_keepalive_connections=100,
    keepalive_expiry=30.0,
)

# Separate connect/read/write/pool so a slow OpenRouter response cannot
# block pool acquisition for other tasks. The `pool=5.0` is the missing
# setting that, without it, makes pool exhaustion look like an infinite
# hang. Reference: https://github.com/encode/httpx/discussions/2418
_TIMEOUT = httpx.Timeout(
    connect=10.0,
    read=300.0,     # OpenRouter Opus can stream long responses
    write=10.0,
    pool=5.0,       # fail fast on pool exhaustion
)

# Per-PROVIDER semaphores. Singleton per event loop. Sized to upstream
# tolerance, not fanout dream. 24 is conservative under OpenRouter +
# Anthropic upstream throttling for Opus 4.7.
_OPENROUTER_SEM_SIZE = 24
_ANTHROPIC_SEM_SIZE = 20


# ── Per-event-loop singletons ─────────────────────────────────────────────
# We key by the running event loop so an Inngest worker that spawns a fresh
# loop per job gets a fresh client (prevents "Future attached to a
# different loop" errors). Empirically Railway's uvicorn `--workers 1`
# has exactly one loop, so this dict stays at size 1.
# 2026-05-26 — WeakKeyDictionary so a closed event loop (test runner doing
# `asyncio.run()` per-test; Inngest Connect spawning short-lived loops)
# gets garbage-collected along with its httpx pool. Strong-ref dicts
# would leak 200-connection httpx pools + provider semaphores per loop
# (python-reviewer HIGH-1, 2026-05-26 perf wave 2 review).
_clients: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, httpx.AsyncClient]" = (
    weakref.WeakKeyDictionary()
)
_openrouter_semaphores: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = (
    weakref.WeakKeyDictionary()
)
_anthropic_semaphores: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = (
    weakref.WeakKeyDictionary()
)


def get_async_client() -> httpx.AsyncClient:
    """Return the shared ``AsyncClient`` for the current event loop.

    Creates one on first call; subsequent calls reuse it so TLS handshakes,
    DNS lookups, and TCP connections are amortised across the pipeline.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError as e:
        raise RuntimeError(
            "get_async_client() must be called from inside a running event loop"
        ) from e

    client = _clients.get(loop)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(
            limits=_LIMITS,
            timeout=_TIMEOUT,
            http2=False,  # encode/httpx#2418 — max_connections does NOT cap H/2 streams
            # Let tenacity own retries (see app/resilience.py). httpx's
            # transport-level retries would double-up.
            transport=httpx.AsyncHTTPTransport(retries=0),
        )
        _clients[loop] = client
        log.info(
            "http_clients: AsyncClient created (limits=%d/%d keepalive=%.0fs)",
            _LIMITS.max_connections,
            _LIMITS.max_keepalive_connections,
            _LIMITS.keepalive_expiry,
        )
    return client


def openrouter_semaphore() -> asyncio.Semaphore:
    """Process-wide concurrency cap for OpenRouter POSTs.

    Sized to OpenRouter + Anthropic upstream tolerance for Opus 4.7
    (≈20-30 concurrent). Under heavy fanout (e.g. an 88-rule phrase
    pack across 5 pipelines) the semaphore queues calls at THIS
    boundary — preventing the httpcore pool + OpenRouter rate limit
    from being slammed simultaneously.
    """
    loop = asyncio.get_running_loop()
    sem = _openrouter_semaphores.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(_OPENROUTER_SEM_SIZE)
        _openrouter_semaphores[loop] = sem
    return sem


def anthropic_semaphore() -> asyncio.Semaphore:
    """Same as ``openrouter_semaphore`` for direct Anthropic API calls."""
    loop = asyncio.get_running_loop()
    sem = _anthropic_semaphores.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(_ANTHROPIC_SEM_SIZE)
        _anthropic_semaphores[loop] = sem
    return sem


async def aclose_all_clients() -> None:
    """Close every cached AsyncClient. Call from FastAPI lifespan shutdown."""
    for client in list(_clients.values()):
        try:
            await client.aclose()
        except Exception as e:  # noqa: BLE001 — shutdown is best-effort
            log.warning("http_clients: aclose failed: %r", e)
    _clients.clear()
    _openrouter_semaphores.clear()
    _anthropic_semaphores.clear()
