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
    _is_retryable,
    _is_statement_timeout,
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


# ─── _is_statement_timeout classifier (D9 fix, 2026-05-26) ──────────────────


class TestIsStatementTimeout:
    @pytest.mark.parametrize(
        "msg",
        [
            "canceling statement due to statement timeout",
            "(psycopg2.errors.QueryCanceled) canceling statement due to statement timeout",
        ],
    )
    def test_statement_timeout_signatures(self, msg: str) -> None:
        e = OperationalError(statement="UPDATE calls SET x=1", params={}, orig=psycopg2.OperationalError(msg))
        assert _is_statement_timeout(e) is True

    def test_user_cancel_not_classified_as_timeout(self) -> None:
        """User-initiated `pg_cancel_backend()` carries the same SQLSTATE
        as statement_timeout (57014) but a different wire message — and
        must NOT be retried (an admin asked us to stop). Confirms the
        2026-05-26 reviewer C1 fix: drop the broad `"querycanceled"`
        signature in favour of the precise wire message."""
        e = OperationalError(statement="SELECT 1", params={}, orig=psycopg2.OperationalError(
            "canceling statement due to user request"
        ))
        assert _is_statement_timeout(e) is False

    def test_disconnect_message_is_not_statement_timeout(self) -> None:
        """Disconnects and timeouts must not be confused — the retry
        policy differs (timeout retries longer to clear lock contention)."""
        e = OperationalError(statement="SELECT 1", params={}, orig=psycopg2.OperationalError(
            "SSL connection has been closed unexpectedly"
        ))
        assert _is_statement_timeout(e) is False

    def test_value_error_not_statement_timeout(self) -> None:
        assert _is_statement_timeout(ValueError("boom")) is False

    def test_is_retryable_covers_both_classes(self) -> None:
        disc = OperationalError(statement="SELECT 1", params={}, orig=psycopg2.OperationalError(
            "server closed the connection unexpectedly"
        ))
        tout = OperationalError(statement="UPDATE x SET y=1", params={}, orig=psycopg2.OperationalError(
            "canceling statement due to statement timeout"
        ))
        bug = OperationalError(statement="SELECT 1", params={}, orig=psycopg2.OperationalError(
            "duplicate key value violates unique constraint"
        ))
        assert _is_retryable(disc) is True
        assert _is_retryable(tout) is True
        assert _is_retryable(bug) is False


class TestStatementTimeoutRetry:
    """Real-prod incident (2026-05-26 D9): 3 concurrent pipelines mutating
    the same deal-stub row serialised each other's UPDATE on the call row;
    one of every 3 calls hit ``statement_timeout`` and landed at
    ``status=failed``. The retry path turns that into a recoverable
    transient by giving the sibling time to release the lock."""

    def test_statement_timeout_retries_and_recovers(self) -> None:
        calls = {"n": 0}

        @db_retry_on_disconnect(base_delay_s=0.0)
        def fn() -> str:
            calls["n"] += 1
            if calls["n"] < 3:
                raise OperationalError(
                    statement="UPDATE calls SET filename=:f WHERE id=:i",
                    params={"f": "x.mp3", "i": "abc"},
                    orig=psycopg2.OperationalError(
                        "canceling statement due to statement timeout"
                    ),
                )
            return "recovered"

        assert fn() == "recovered"
        assert calls["n"] == 3  # initial + 2 retries

    def test_statement_timeout_exhausts_after_max_attempts(self) -> None:
        calls = {"n": 0}

        @db_retry_on_disconnect(max_attempts=3, base_delay_s=0.0)
        def fn() -> str:
            calls["n"] += 1
            raise OperationalError(
                statement="UPDATE calls SET filename=:f",
                params={"f": "x.mp3"},
                orig=psycopg2.OperationalError(
                    "canceling statement due to statement timeout"
                ),
            )

        with pytest.raises(OperationalError):
            fn()
        assert calls["n"] == 3

    def test_direct_psycopg2_querycanceled_recognised(self) -> None:
        """Defence-in-depth: raw ``psycopg2.errors.QueryCanceled`` (not
        wrapped by SQLAlchemy) must still be classified as a statement
        timeout. Bare-cursor callsites and future refactors that strip
        the wrapper would otherwise silently bypass the retry."""
        import psycopg2.errors
        # Sentinel construction varies across psycopg2 versions; the
        # canonical path is `pg_cursor.execute` raising the class with
        # an SQLSTATE-bound message. We mimic that without a live DB
        # by instantiating with the wire-message string.
        e = psycopg2.errors.QueryCanceled("canceling statement due to statement timeout")
        assert _is_statement_timeout(e) is True


class TestTraceStepRetry:
    """In-step retry path inside ``pipeline._trace_step``. Separate from
    the decorator path because the step retry uses ``asyncio.sleep`` not
    ``time.sleep``, publishes a ``step_retry`` SSE event on each retry,
    and lives inside the pipeline_step_log lifecycle. Mocks
    `_persist_step_*`, `_mark_step_started`, and `realtime.publish` to
    isolate the retry logic from the persistence/SSE plumbing."""

    @pytest.mark.asyncio
    async def test_step_retry_recovers_after_statement_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Step fn raises statement_timeout once, succeeds on attempt 2.
        Verifies: function called twice; ``step_retry`` published once;
        ``step_ok`` published once; ``step_err`` NOT published."""
        from app import pipeline

        published: list[tuple[str, str]] = []
        monkeypatch.setattr(pipeline, "_random",
                            type("R", (), {"uniform": staticmethod(lambda a, b: 0.0)})())
        monkeypatch.setattr(
            pipeline.realtime, "publish",
            lambda call_id, etype, payload: published.append((call_id, etype)),
        )
        monkeypatch.setattr(
            "app.workflows.process_call._persist_step_running",
            lambda *a, **kw: "row-1",
        )
        monkeypatch.setattr(
            "app.workflows.process_call._persist_step_done",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            "app.workflows.process_call._mark_step_started",
            lambda *a, **kw: None,
        )

        attempts = {"n": 0}

        def fn() -> str:
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise OperationalError(
                    statement="UPDATE customer_deals SET customer_name=:c",
                    params={"c": "X"},
                    orig=psycopg2.OperationalError(
                        "canceling statement due to statement timeout"
                    ),
                )
            return "ok"

        result = await pipeline._trace_step("call-1", "detect_metadata", fn)
        assert result == "ok"
        assert attempts["n"] == 2
        kinds = [e[1] for e in published]
        assert kinds.count("step_retry") == 1
        assert kinds.count("step_ok") == 1
        assert "step_err" not in kinds

    @pytest.mark.asyncio
    async def test_step_retry_exhausts_publishes_step_err(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Step fn raises statement_timeout on ALL 3 attempts. Verifies:
        function called 3 times; ``step_retry`` published twice (between
        the 3 attempts); ``step_err`` published once at the outer catch;
        exception propagates."""
        from app import pipeline

        published: list[tuple[str, str]] = []
        monkeypatch.setattr(pipeline, "_random",
                            type("R", (), {"uniform": staticmethod(lambda a, b: 0.0)})())
        monkeypatch.setattr(
            pipeline.realtime, "publish",
            lambda call_id, etype, payload: published.append((call_id, etype)),
        )
        monkeypatch.setattr(
            "app.workflows.process_call._persist_step_running",
            lambda *a, **kw: "row-1",
        )
        monkeypatch.setattr(
            "app.workflows.process_call._persist_step_done",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            "app.workflows.process_call._mark_step_started",
            lambda *a, **kw: None,
        )

        attempts = {"n": 0}

        def fn() -> str:
            attempts["n"] += 1
            raise OperationalError(
                statement="UPDATE customer_deals SET customer_name=:c",
                params={"c": "X"},
                orig=psycopg2.OperationalError(
                    "canceling statement due to statement timeout"
                ),
            )

        with pytest.raises(OperationalError):
            await pipeline._trace_step("call-2", "detect_metadata", fn)
        assert attempts["n"] == 3  # initial + 2 retries
        kinds = [e[1] for e in published]
        assert kinds.count("step_retry") == 2
        assert kinds.count("step_err") == 1
        assert "step_ok" not in kinds

    @pytest.mark.asyncio
    async def test_step_non_retryable_exception_propagates_immediately(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Step fn raises a real bug (constraint violation). The retry
        path must NOT fire — exception propagates on first attempt."""
        from app import pipeline

        published: list[tuple[str, str]] = []
        monkeypatch.setattr(
            pipeline.realtime, "publish",
            lambda call_id, etype, payload: published.append((call_id, etype)),
        )
        monkeypatch.setattr(
            "app.workflows.process_call._persist_step_running",
            lambda *a, **kw: "row-1",
        )
        monkeypatch.setattr(
            "app.workflows.process_call._persist_step_done",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            "app.workflows.process_call._mark_step_started",
            lambda *a, **kw: None,
        )

        attempts = {"n": 0}

        def fn() -> str:
            attempts["n"] += 1
            raise OperationalError(
                statement="INSERT INTO calls (id) VALUES (:i)",
                params={"i": "dup"},
                orig=psycopg2.OperationalError(
                    "duplicate key value violates unique constraint"
                ),
            )

        with pytest.raises(OperationalError):
            await pipeline._trace_step("call-3", "detect_metadata", fn)
        assert attempts["n"] == 1  # NO retry
        assert "step_retry" not in [e[1] for e in published]
        assert "step_err" in [e[1] for e in published]


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
    behaviour. The original 25/50 config sat idle past Supavisor's
    ~5min kill window and produced mid-query SSL disconnects; the
    2026-05-25 retune dropped to 10/20 to fit under that window.

    Bounds (2026-05-27 retune, post bulk-upload soak test):
      pool_size       ≤ 25   — Supavisor multiplexes; warm pool stays small
                               but the score+finalize WORKFLOW_STEP path
                               under 9-way burst exhausted 10/20=30, so the
                               cap was lifted to 25/50=75. TCP keepalives
                               + pool_recycle≤1800 + tcp_user_timeout=10s
                               now mitigate the original idle-kill issue,
                               so a slightly warmer pool is safe.
      max_overflow    ≤ 50   — burst headroom for bulk-upload concurrency.
      pool_recycle    ≤ 1800 — beat Supavisor's idle-kill window.
    """

    def test_pool_size_capped(self) -> None:
        pool = db_module.engine.pool
        assert pool.size() <= 25, (
            f"pool_size={pool.size()} exceeds Supavisor-safe cap (25). "
            "Larger pools sit idle past Supavisor's ~5min kill window and "
            "produce mid-query SSL disconnects."
        )

    def test_max_overflow_capped(self) -> None:
        pool = db_module.engine.pool
        max_overflow = getattr(pool, "_max_overflow", 20)
        assert max_overflow <= 50, (
            f"max_overflow={max_overflow} exceeds Supavisor-safe cap (50)."
        )

    def test_recycle_under_supavisor_kill_window(self) -> None:
        """2026-05-26 perf wave bumped pool_recycle from 240 s → 1800 s
        deliberately because TCP keepalives (``tcp_user_timeout=10000``)
        now detect dead connections mid-query, so the pool_recycle
        window's sole job is to flush long-idle connections during quiet
        periods. Anything ≤ 1800 s satisfies that contract; anything
        higher risks Supavisor's idle-kill window if keepalives ever
        get disabled. Re-baselined assertion lives here so future
        drift gets caught — but the bound is now 1800 not 600.
        """
        pool = db_module.engine.pool
        recycle = getattr(pool, "_recycle", 300)
        assert recycle <= 1800, (
            f"pool_recycle={recycle}s exceeds the post-2026-05-26 keepalive-paired ceiling (1800s). "
            "Bump TCP keepalives in connect_args before raising further."
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
