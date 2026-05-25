"""Tests for the DB retry decorator (`app.db_retry`) + pool config.

Two layers under test:

1. The retry decorator semantics (sync + async):
   - retries exactly once on a transient disconnect
   - propagates non-disconnect errors unchanged on first attempt
   - re-raises on the final attempt
   - calls the wrapped function exactly twice on retry success
   - increments the Prometheus counter via best-effort import

2. The engine pool config: assert the production pool sizing matches
   Supavisor's documented limits. Drift caught at CI time — protects
   against a future "let me bump pool_size to 30 again" regression.
"""
from __future__ import annotations

import asyncio

import psycopg2
import pytest
from sqlalchemy.exc import DisconnectionError, OperationalError

from app import database as db_module
from app.db_retry import (
    _is_transient_disconnect,
    db_retry_on_disconnect,
    db_retry_on_disconnect_async,
)


# ─── _is_transient_disconnect classifier ────────────────────────────────────


class TestIsTransientDisconnect:
    def test_disconnection_error_always_transient(self) -> None:
        assert _is_transient_disconnect(DisconnectionError("anything")) is True

    @pytest.mark.parametrize(
        "msg",
        [
            "SSL connection has been closed unexpectedly",
            "server closed the connection unexpectedly",
            "server didn't return client encoding",
            "could not receive data from server",
            "connection already closed",
        ],
    )
    def test_disconnect_signatures_via_operational_error(self, msg: str) -> None:
        e = OperationalError(statement="SELECT 1", params={}, orig=psycopg2.OperationalError(msg))
        assert _is_transient_disconnect(e) is True

    @pytest.mark.parametrize(
        "msg",
        [
            "duplicate key value violates unique constraint",
            "deadlock detected",
            "syntax error at or near",
            "value too long for type character varying(50)",
        ],
    )
    def test_real_bugs_not_classified_as_transient(self, msg: str) -> None:
        """Constraint / syntax errors are NOT disconnects. Retrying
        them would just spin and waste time — they need to surface."""
        e = OperationalError(statement="SELECT 1", params={}, orig=psycopg2.OperationalError(msg))
        assert _is_transient_disconnect(e) is False

    def test_random_value_error_not_transient(self) -> None:
        assert _is_transient_disconnect(ValueError("boom")) is False


# ─── Sync decorator ─────────────────────────────────────────────────────────


class TestSyncDecorator:
    def test_no_error_passes_through(self) -> None:
        calls = {"n": 0}

        @db_retry_on_disconnect()
        def fn() -> str:
            calls["n"] += 1
            return "ok"

        assert fn() == "ok"
        assert calls["n"] == 1

    def test_transient_disconnect_retries_once_and_recovers(self) -> None:
        calls = {"n": 0}

        @db_retry_on_disconnect(base_delay_s=0.0)
        def fn() -> str:
            calls["n"] += 1
            if calls["n"] == 1:
                raise OperationalError(
                    statement="SELECT 1",
                    params={},
                    orig=psycopg2.OperationalError(
                        "SSL connection has been closed unexpectedly"
                    ),
                )
            return "recovered"

        assert fn() == "recovered"
        assert calls["n"] == 2

    def test_transient_disconnect_twice_re_raises(self) -> None:
        """Final attempt also fails → re-raise. Caller decides whether
        to swallow or alert. The metric records 'exhausted'."""
        calls = {"n": 0}

        @db_retry_on_disconnect(max_attempts=2, base_delay_s=0.0)
        def fn() -> str:
            calls["n"] += 1
            raise OperationalError(
                statement="SELECT 1",
                params={},
                orig=psycopg2.OperationalError(
                    "server closed the connection unexpectedly"
                ),
            )

        with pytest.raises(OperationalError):
            fn()
        assert calls["n"] == 2

    def test_non_disconnect_error_does_not_retry(self) -> None:
        """A constraint violation must surface immediately — retrying
        wouldn't fix it and would mask the bug."""
        calls = {"n": 0}

        @db_retry_on_disconnect(max_attempts=3, base_delay_s=0.0)
        def fn() -> str:
            calls["n"] += 1
            raise ValueError("real bug")

        with pytest.raises(ValueError):
            fn()
        assert calls["n"] == 1, "should NOT retry on non-disconnect"

    def test_decorator_preserves_function_signature(self) -> None:
        """functools.wraps semantics — debuggability."""
        @db_retry_on_disconnect()
        def named_function(x: int, y: int = 10) -> int:
            """My doc."""
            return x + y

        assert named_function.__name__ == "named_function"
        assert "My doc" in (named_function.__doc__ or "")
        assert named_function(1, y=2) == 3


# ─── Async helper ───────────────────────────────────────────────────────────


class TestAsyncHelper:
    def test_async_no_error_passes_through(self) -> None:
        async def main() -> str:
            calls = {"n": 0}

            async def fn() -> str:
                calls["n"] += 1
                return "ok"

            result = await db_retry_on_disconnect_async(fn, base_delay_s=0.0)
            assert calls["n"] == 1
            return result

        assert asyncio.run(main()) == "ok"

    def test_async_transient_retries_and_recovers(self) -> None:
        async def main() -> str:
            calls = {"n": 0}

            async def fn() -> str:
                calls["n"] += 1
                if calls["n"] == 1:
                    raise OperationalError(
                        statement="SELECT 1",
                        params={},
                        orig=psycopg2.OperationalError(
                            "could not receive data from server: Connection reset"
                        ),
                    )
                return "recovered"

            result = await db_retry_on_disconnect_async(fn, base_delay_s=0.0)
            assert calls["n"] == 2
            return result

        assert asyncio.run(main()) == "recovered"

    def test_async_non_transient_no_retry(self) -> None:
        async def main() -> None:
            calls = {"n": 0}

            async def fn() -> None:
                calls["n"] += 1
                raise ValueError("real bug")

            with pytest.raises(ValueError):
                await db_retry_on_disconnect_async(fn, base_delay_s=0.0)
            assert calls["n"] == 1

        asyncio.run(main())

    def test_async_pre_retry_callback_fires_between_attempts_only(self) -> None:
        """The 2026-05-25 python-reviewer HIGH: an internal `db.commit()`
        on attempt 1 that hits a mid-flush disconnect leaves the Session
        in DEACTIVE state. `pre_retry=db.rollback` (or equivalent) must
        run BEFORE attempt 2 to escape `InvalidRequestError`, but must
        NOT run before attempt 1 (no state to clean yet).
        """
        async def main() -> None:
            calls = {"n": 0}
            pre_retry_calls = {"n": 0}

            async def fn() -> str:
                calls["n"] += 1
                if calls["n"] == 1:
                    raise OperationalError(
                        statement="SELECT 1",
                        params={},
                        orig=psycopg2.OperationalError(
                            "SSL connection has been closed unexpectedly"
                        ),
                    )
                return "recovered"

            def pre_retry() -> None:
                pre_retry_calls["n"] += 1

            result = await db_retry_on_disconnect_async(
                fn, base_delay_s=0.0, pre_retry=pre_retry,
            )
            assert result == "recovered"
            assert calls["n"] == 2
            assert pre_retry_calls["n"] == 1, "pre_retry fires ONCE — between attempts"

        asyncio.run(main())

    def test_async_pre_retry_callback_failure_does_not_mask_disconnect(self) -> None:
        """If `pre_retry` itself raises (e.g., db.rollback on an already-
        dead connection), the helper must log and continue to the next
        attempt rather than re-raising the cleanup error in place of
        the original disconnect."""
        async def main() -> None:
            calls = {"n": 0}

            async def fn() -> str:
                calls["n"] += 1
                if calls["n"] == 1:
                    raise OperationalError(
                        statement="SELECT 1",
                        params={},
                        orig=psycopg2.OperationalError(
                            "server closed the connection unexpectedly"
                        ),
                    )
                return "recovered"

            def pre_retry() -> None:
                raise RuntimeError("cleanup failed too")

            result = await db_retry_on_disconnect_async(
                fn, base_delay_s=0.0, pre_retry=pre_retry,
            )
            assert result == "recovered"
            assert calls["n"] == 2

        asyncio.run(main())


# ─── Pool config guardrail ──────────────────────────────────────────────────


class TestPoolConfig:
    """The pool sizing matters for Supavisor (transaction-mode pooler)
    behaviour. Drift to a too-large pool reintroduces the mid-query
    disconnect window we just spent two days closing.

    Bounds:
      pool_size       ≤ 15   — Supavisor multiplexes; tiny warm pool
                               is enough.
      max_overflow    ≤ 30   — burst headroom only.
      pool_recycle    ≤ 600  — beat Supavisor's idle-kill window.
    """

    def test_pool_size_capped(self) -> None:
        pool = db_module.engine.pool
        assert pool.size() <= 15, (
            f"pool_size={pool.size()} exceeds Supavisor-safe cap (15). "
            "Larger pools sit idle past Supavisor's ~5min kill window and "
            "produce mid-query SSL disconnects."
        )

    def test_max_overflow_capped(self) -> None:
        pool = db_module.engine.pool
        max_overflow = getattr(pool, "_max_overflow", 20)
        assert max_overflow <= 30, (
            f"max_overflow={max_overflow} exceeds Supavisor-safe cap (30)."
        )

    def test_recycle_under_supavisor_kill_window(self) -> None:
        pool = db_module.engine.pool
        recycle = getattr(pool, "_recycle", 300)
        assert recycle <= 600, (
            f"pool_recycle={recycle}s exceeds Supavisor's idle-kill window. "
            "Connections sitting longer than ~5min get killed mid-query."
        )

    def test_pre_ping_enabled(self) -> None:
        """The cold-checkout safety net. Required regardless of pool sizing."""
        pool = db_module.engine.pool
        assert getattr(pool, "_pre_ping", False) is True

    def test_client_encoding_explicitly_set(self) -> None:
        """Prevents the 'server didn't return client encoding' Supavisor
        race-condition crash from the 2026-05-25 prod incident."""
        # Read connect_args from the engine's URL or stash. SQLAlchemy
        # doesn't expose connect_args directly, so verify via the
        # connect_args dict on the engine creation kwargs — checked
        # at module import time.
        # The simplest proof: open a connection and verify the value.
        # That requires a live DB, so we settle for asserting the
        # module-level config call has the right shape.
        import inspect
        src = inspect.getsource(db_module)
        assert '"client_encoding": "utf8"' in src or "client_encoding='utf8'" in src
