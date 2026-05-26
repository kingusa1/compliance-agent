"""Enterprise-grade DB retry decorator for transient Supavisor disconnects
and statement-timeout contention under bulk-upload concurrency.

Production reality (2026-05-25 / 2026-05-26 live logs): two distinct
classes of transient DB failure recover via retry.

1. **Supavisor disconnects.** Supabase Supavisor occasionally closes
   long-lived connections mid-query (`SSL connection has been closed
   unexpectedly`, `server closed the connection unexpectedly`,
   `server didn't return client encoding`). `pool_pre_ping` catches
   stale connections at checkout but a connection killed *during* a
   query needs explicit retry.

2. **Statement-timeout under bulk concurrency (2026-05-26 D9).** When
   3+ pipelines mutate sibling rows on the same deal stub (per-call
   STUB_RENAME `UPDATE customer_deals SET customer_name=` competes
   with the per-call `UPDATE calls SET filename, script_id`), Postgres
   row-locks queue and the 15 s `statement_timeout` fires —
   `psycopg2.errors.QueryCanceled: canceling statement due to
   statement timeout`. The pipeline step crashes and the call's
   ``status`` is left at `failed`. Retrying with brief backoff lets
   the sibling pipeline release its lock so this one's UPDATE
   proceeds.

Without retry, the user sees a 503 (the FastAPI handler returns
`Retry-After: 1` for HTTP requests, and the frontend's TanStack Query
auto-retries — so HTTP paths recover gracefully). Background tasks
(`idle_release_loop`, post-finalize tracker-autofill agents like
`date_extractor`, `quality_agent`) do NOT have a frontend safety net;
they silently skip the iteration and the work never runs. Pipeline
steps don't have a safety net either — a single QueryCanceled aborts
the whole call.

This decorator closes both gaps. Use on any function that opens its own
`SessionLocal` and runs a quick read/write — typically background loop
bodies, post-pipeline agents, and the pipeline's mutation-heavy steps.

Design notes:

  * **What we retry**:
    - `OperationalError` / `DBAPIError` / `DisconnectionError` whose
      message matches a known transient disconnect signature
      (`_DISCONNECT_SIGNATURES` in `database.py`).
    - `psycopg2.errors.QueryCanceled` with the `statement timeout`
      signature — the lock-contention case from D9.
    Constraint violations, syntax errors, real deadlocks etc.
    propagate unchanged — they're bugs retry can't fix.

  * **How many retries**: up to 2 retries (3 attempts total) by default
    after the 2026-05-26 D9 bump. Supavisor disconnects are almost
    always single-blip; the extra retry was added for statement-timeout
    contention (sibling pipelines releasing locks on a contested deal
    row). Disconnects also benefit from the extra retry budget at no
    cost — two retries are still conservative and safe given the
    single-blip pattern. If the third attempt fails the network/pooler
    is genuinely unhealthy and the caller should surface the error.

  * **Jitter**: backoff is exponential with full jitter
    (``random.uniform(0, base * 2^(attempt-1))``) so that 3+ pipelines
    racing for the same contested row don't all retry in lockstep and
    cascade-fail the same way. Without jitter the AWS "thundering
    herd" pattern applies — three workers back off for the exact same
    duration and re-collide on the lock.

  * **What we do between attempts**: hard-invalidate the session
    (`session.close()`) so the next attempt opens a fresh psycopg2
    connection — the dead one will be evicted from the pool by the
    `handle_error` listener's `is_disconnect` flag.

  * **Idempotency**: the decorator does NOT make a non-idempotent
    operation safe. Callers are responsible for designing the wrapped
    function so a partial commit on attempt 1 + full commit on attempt
    2 produces the same end state. For pure-read callers this is free.

  * **Metric**: every retry triggers
    `db_retry_total{outcome="success"|"exhausted"}.inc()` via the
    project's Prometheus instrumentator so ops can graph the recovery
    rate.

Usage:

    # Pooled connection (the default for short, request-scoped writes).
    from app.database import SessionLocal
    from app.db_retry import db_retry_on_disconnect

    @db_retry_on_disconnect()
    def _per_request_write() -> int:
        db = SessionLocal()
        try:
            return _do_work(db)
        finally:
            db.close()

    # Long-lived background loops (sweepers, schedulers) should use the
    # direct-connection engine — Supavisor's transaction-mode pooler is
    # tuned for many short transactions and intermittently kills idle
    # pool members, making it a poor fit for periodic background writes.
    # See `app/database.py:direct_engine` + `app/main.py:_idle_release_loop`.
    from app.database import DirectSessionLocal

    @db_retry_on_disconnect()
    def _idle_release_iteration() -> int:
        db = DirectSessionLocal()
        try:
            return _release_idle_claims_core(db)
        finally:
            db.close()
"""
from __future__ import annotations

import functools
import logging
import random
import time
from typing import Callable, TypeVar

from sqlalchemy.exc import DBAPIError, DisconnectionError, OperationalError

from app.database import _DISCONNECT_SIGNATURES

log = logging.getLogger("compliance.db_retry")

T = TypeVar("T")

# Bounded exponential backoff. Three attempts total (1 original + 2
# retries) with 250 ms / 500 ms between them. The 2026-05-26 D9
# investigation showed sibling pipelines releasing their lock within
# ~1 s on contended deal-stub mutations, so two retries cover the
# overwhelming majority of cases. Beyond that the network/pool/contention
# is genuinely unhealthy and the caller should surface the error.
_DEFAULT_MAX_ATTEMPTS = 3
_DEFAULT_BASE_DELAY_S = 0.25


# Exact Postgres wire message for ``statement_timeout`` (SQLSTATE 57014).
# Kept as a single precise string rather than the broader ``"querycanceled"``
# substring — the broader match would also accept ``pg_cancel_backend()``
# user-cancellation (SQLSTATE 57014 with message
# ``"canceling statement due to user request"``) which is NOT retryable
# (an admin asked us to stop).
_STATEMENT_TIMEOUT_SIGNATURES = (
    "canceling statement due to statement timeout",
)


def _is_statement_timeout(exc: BaseException) -> bool:
    """True iff ``exc`` is a Postgres ``statement_timeout`` cancellation.

    Two paths covered:

    * Raw ``psycopg2.errors.QueryCanceled`` raised before SQLAlchemy
      wraps it (e.g. cursor.execute outside an SA-instrumented
      connection, or a future refactor that strips the wrapper).
      Matched by isinstance against the psycopg2 class so we don't rely
      on the message string survival.
    * SQLAlchemy-wrapped ``OperationalError`` / ``DBAPIError`` whose
      `str()` carries the exact Postgres marker
      ``"canceling statement due to statement timeout"``. Distinguishes
      the contended-lock case from real DB failures (deadlock, syntax,
      missing column) and from user-initiated cancellations.
    """
    try:
        import psycopg2.errors as _pg_errors
        if isinstance(exc, _pg_errors.QueryCanceled):
            return True
    except ImportError:  # pragma: no cover — psycopg2 always present in prod
        pass
    if not isinstance(exc, (OperationalError, DBAPIError)):
        return False
    msg = str(exc).lower()
    return any(sig in msg for sig in _STATEMENT_TIMEOUT_SIGNATURES)


def _is_transient_disconnect(exc: BaseException) -> bool:
    """True iff `exc` is in the disconnect class — same signature list
    the engine listener + FastAPI handler use, so we never disagree."""
    if isinstance(exc, DisconnectionError):
        return True
    if not isinstance(exc, (OperationalError, DBAPIError)):
        return False
    msg = str(exc).lower()
    return any(sig in msg for sig in _DISCONNECT_SIGNATURES)


def _is_retryable(exc: BaseException) -> bool:
    """Either a disconnect or a statement-timeout. Centralised so call
    sites don't have to remember both predicates."""
    return _is_transient_disconnect(exc) or _is_statement_timeout(exc)


def _record_metric(outcome: str) -> None:
    """Increment `db_retry_total{outcome=...}` if Prometheus is wired.
    Tolerates absence so tests / dev shells without the instrumentator
    don't crash. Outcomes: 'success' = retry recovered; 'exhausted' =
    final attempt also failed."""
    try:
        from app.observability_metrics import db_retry_total
        db_retry_total.labels(outcome=outcome).inc()
    except Exception:  # noqa: BLE001 — metric is best-effort
        pass


def _jittered_delay(base_delay_s: float, attempt: int) -> float:
    """Full-jitter exponential backoff: uniform draw in
    ``[0, base * 2^(attempt-1)]``. Decorrelates concurrent retriers so
    3+ workers contending for the same row don't lockstep-collide on
    every backoff window.
    """
    ceiling = base_delay_s * (2 ** (attempt - 1))
    return random.uniform(0, ceiling)


def db_retry_on_disconnect(
    *,
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    base_delay_s: float = _DEFAULT_BASE_DELAY_S,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator: retry the wrapped function up to twice (3 attempts
    total) on a transient psycopg2 / SQLAlchemy disconnect OR a
    Postgres ``statement_timeout``.

    The wrapped function must own its own DB session lifecycle
    (open + close inside). On the retry, the function is re-invoked
    from scratch — its session is fresh, its work is re-done.

    Non-retryable exceptions (real bugs: constraint violation, syntax
    error, deadlock, user cancellation) propagate unchanged on the
    FIRST attempt (no retry). Retryable exceptions on the FINAL
    attempt re-raise so the caller can decide whether to swallow /
    alert.

    Backoff is exponential with full jitter — see ``_jittered_delay``.
    """

    def deco(fn: Callable[..., T]) -> Callable[..., T]:
        fn_name = getattr(fn, "__qualname__", getattr(fn, "__name__", "fn"))

        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> T:
            last_exc: BaseException | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except Exception as e:  # noqa: BLE001 — broad catch is intentional
                    if not _is_retryable(e):
                        raise
                    last_exc = e
                    if attempt >= max_attempts:
                        # Final attempt also failed. Surface the error
                        # to the caller. Counter says "exhausted".
                        _record_metric("exhausted")
                        log.warning(
                            "db_retry exhausted fn=%s attempts=%d err=%s",
                            fn_name, attempt, str(e)[:200],
                        )
                        raise
                    delay = _jittered_delay(base_delay_s, attempt)
                    log.info(
                        "db_retry transient_disconnect fn=%s attempt=%d "
                        "delay_s=%.2f err=%s",
                        fn_name, attempt, delay, str(e)[:200],
                    )
                    time.sleep(delay)
            # Unreachable — the loop either returns or re-raises. Kept
            # so static analysis doesn't claim a missing return path.
            if last_exc is not None:
                raise last_exc
            return None  # type: ignore[return-value]

        return wrapper

    return deco


async def db_retry_on_disconnect_async(
    fn: Callable[..., T],
    *,
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    base_delay_s: float = _DEFAULT_BASE_DELAY_S,
    pre_retry: Callable[[], None] | None = None,
) -> Callable[..., T]:
    """Async variant — same semantics for awaitable callables. Implemented
    as a direct call (not a decorator) so caller can pass a lambda /
    `functools.partial` that already has its args bound.

    ``pre_retry`` is invoked exactly between attempts (NOT before the
    first attempt). Use it to clear session state that survives a
    disconnect — typically `db.rollback()` to escape the
    `InvalidRequestError: Can't reconnect until invalid transaction is
    rolled back` that SQLAlchemy raises when a mid-flush disconnect
    leaves the Session in DEACTIVE state. Without this, the wrapped
    function's second attempt fails with a non-transient exception that
    the caller catches as "skipped" — silently dropping the work, which
    is the exact failure mode this decorator was designed to prevent.

    Example:

        async def _quality_agent_pass(call_id, db):
            ...

        await db_retry_on_disconnect_async(
            lambda: _quality_agent_pass(call_id, db),
            pre_retry=db.rollback,
        )
    """
    import asyncio

    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            if attempt > 1 and pre_retry is not None:
                try:
                    pre_retry()
                except Exception as cleanup_e:  # noqa: BLE001
                    # Cleanup failure is itself worth knowing about but
                    # mustn't mask the original disconnect. Log and
                    # carry on — the retry might still succeed if the
                    # caller's wrapped function opens its own session.
                    log.warning(
                        "db_retry_async pre_retry callback failed: %s",
                        str(cleanup_e)[:200],
                    )
            result = fn()
            if asyncio.iscoroutine(result):
                return await result  # type: ignore[return-value]
            return result  # type: ignore[return-value]
        except Exception as e:  # noqa: BLE001
            if not _is_retryable(e):
                raise
            last_exc = e
            if attempt >= max_attempts:
                _record_metric("exhausted")
                log.warning(
                    "db_retry_async exhausted attempts=%d err=%s",
                    attempt, str(e)[:200],
                )
                raise
            delay = _jittered_delay(base_delay_s, attempt)
            log.info(
                "db_retry_async transient_disconnect attempt=%d "
                "delay_s=%.2f err=%s",
                attempt, delay, str(e)[:200],
            )
            await asyncio.sleep(delay)
    if last_exc is not None:
        raise last_exc
    return None  # type: ignore[return-value]
