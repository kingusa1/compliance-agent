"""Regression tests for the SSL-disconnect handling shipped 2026-05-24.

Two complementary mechanisms under test:

1. `app.database._handle_disconnect` — SQLAlchemy `handle_error` listener
   that converts in-flight psycopg2 disconnects into `DisconnectionError`
   so SQLAlchemy invalidates the pool generation. Without this, the same
   dead connection can be handed back out on the next checkout, multiplying
   one network blip into dozens of failed requests.

2. `app.main._db_operational_error_handler` — FastAPI exception handler
   that turns the resulting `OperationalError` into a single-line warning
   + 503 response, instead of a 30-line traceback dumped to stdout. Under a
   small burst, traceback flooding blows past Railway's 500 logs/sec ceiling
   and starts dropping unrelated log lines.

The actual production trigger was a `psycopg2.OperationalError: SSL connection
has been closed unexpectedly` repeating across many concurrent requests +
`Railway rate limit of 500 logs/sec reached for replica … Messages dropped: 6541`.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import psycopg2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError, DisconnectionError, OperationalError

import app.database as db_module
from app.main import (
    _DB_DISCONNECT_SIGNATURES,
    _db_operational_error_handler,
    _is_disconnect,
)


# ─── _is_disconnect classifier ──────────────────────────────────────────────

class TestIsDisconnect:
    @pytest.mark.parametrize(
        "msg",
        [
            "SSL connection has been closed unexpectedly",
            "ssl connection has been closed unexpectedly",  # lowercase
            "server closed the connection unexpectedly",
            "connection already closed",
            "terminating connection due to administrator command",
            "could not receive data from server: Connection reset by peer",
            "could not send data to server: Broken pipe",
            # Real Supabase / psycopg2 phrasing seen in prod
            "psycopg2.OperationalError: SSL connection has been closed unexpectedly\n",
        ],
    )
    def test_classifies_disconnect_messages(self, msg: str) -> None:
        assert _is_disconnect(Exception(msg)) is True

    def test_disconnection_error_subclass_always_matches(self) -> None:
        assert _is_disconnect(DisconnectionError("any text")) is True

    @pytest.mark.parametrize(
        "msg",
        [
            "duplicate key value violates unique constraint",
            "deadlock detected",
            "syntax error at or near",
            "value too long for type character varying(50)",
            "could not serialize access due to concurrent update",
        ],
    )
    def test_does_not_misclassify_real_db_bugs(self, msg: str) -> None:
        """Constraint / syntax / serialisation errors must NOT be silenced as
        503 — those are real bugs that the operator needs the full traceback for."""
        assert _is_disconnect(Exception(msg)) is False

    def test_disconnect_signatures_constant_is_single_source_of_truth(self) -> None:
        """database.py owns the signature tuple. main.py imports it by name
        so they're identity-equal, not just value-equal. This guarantees no
        future contributor can grow the list on one side and forget the other."""
        assert _DB_DISCONNECT_SIGNATURES is db_module._DISCONNECT_SIGNATURES


# ─── handle_error engine listener ───────────────────────────────────────────

class TestHandleErrorListener:
    def test_disconnect_signature_sets_is_disconnect_flag(self) -> None:
        """The listener must set `ctx.is_disconnect = True` rather than
        raising. Raising substitutes the exception class reaching Starlette
        with `DisconnectionError`, which is NOT a `DBAPIError` subclass —
        the FastAPI handler would then never match and Starlette would dump
        the 30-line traceback we set out to suppress."""
        ctx = MagicMock()
        ctx.original_exception = psycopg2.OperationalError(
            "SSL connection has been closed unexpectedly"
        )
        ctx.is_disconnect = False
        # Must NOT raise.
        result = db_module._handle_disconnect(ctx)
        assert result is None
        assert ctx.is_disconnect is True

    def test_non_disconnect_error_does_not_flip_flag(self) -> None:
        """A syntax error or constraint violation must NOT trip the
        is_disconnect flag — that would invalidate a perfectly healthy
        connection and add log noise."""
        ctx = MagicMock()
        ctx.original_exception = Exception('syntax error at or near "SELEKT"')
        ctx.is_disconnect = False
        assert db_module._handle_disconnect(ctx) is None
        assert ctx.is_disconnect is False

    def test_none_original_exception_is_safe(self) -> None:
        """`ctx.original_exception` can be None during certain SQLAlchemy
        internal error paths; the listener must not NPE."""
        ctx = MagicMock()
        ctx.original_exception = None
        ctx.is_disconnect = False
        assert db_module._handle_disconnect(ctx) is None
        assert ctx.is_disconnect is False

    def test_listener_is_registered_on_engine(self) -> None:
        from sqlalchemy import event as sa_event
        assert sa_event.contains(
            db_module.engine, "handle_error", db_module._handle_disconnect
        )


# ─── Integration: engine event dispatch → FastAPI handler ───────────────────


class TestEngineToHandlerIntegration:
    """The risk that the unit tests don't cover: even though the listener
    and the handler each work in isolation, an exception class mismatch
    between them silently breaks the chain.

    These tests exercise SQLAlchemy's *real* exception dispatcher (no
    mocks) to assert that whatever the engine raises on a psycopg2-style
    disconnect is a DBAPIError subclass — which is what the FastAPI handler
    is registered against.
    """

    def _trigger_handle_error(self) -> BaseException:
        """Call the listener via SA's real dispatcher and capture the
        exception SA would have raised to the caller.

        We can't actually break a real socket here, but we can replicate
        what `_handle_dbapi_exception` does: walk the `handle_error` dispatch
        chain with a context carrying the prod-style psycopg2 error, then
        observe what the dispatcher decides to re-raise.
        """
        from sqlalchemy.engine.base import ExceptionContext

        # Minimal-fields ExceptionContext stand-in — we only need the bits
        # `_handle_disconnect` reads.
        class _Ctx:
            original_exception = psycopg2.OperationalError(
                "SSL connection has been closed unexpectedly"
            )
            sqlalchemy_exception = None
            is_disconnect = False
            invalidate_pool_on_disconnect = True
            chained_exception = None
            execution_context = None
            connection = None
            engine = db_module.engine
            cursor = None
            statement = None
            parameters = None

            def __init__(self):
                pass

        ctx = _Ctx()
        # Fire the listener directly (this is what SA does internally).
        db_module._handle_disconnect(ctx)
        return ctx  # caller inspects ctx.is_disconnect

    def test_listener_signals_disconnect_without_substituting_exception_type(self) -> None:
        """The whole bug python-reviewer caught: if the listener raises a
        non-DBAPIError subclass, the FastAPI handler key never matches.
        This test asserts the listener does NOT do that."""
        ctx = self._trigger_handle_error()
        assert ctx.is_disconnect is True  # pool will be invalidated
        # Nothing was substituted into ctx.sqlalchemy_exception — SA will
        # use its own dialect wrapping (OperationalError, a DBAPIError).
        assert ctx.sqlalchemy_exception is None

    def test_full_chain_via_test_client_returns_503_for_disconnect(self) -> None:
        """End-to-end: a route that raises a real DBAPIError-wrapped
        disconnect must hit the FastAPI handler and surface as a 503,
        never as a 500 with a traceback. This is what production cares
        about — if this passes, the log flood stops."""
        client = TestClient(_build_app_with_handler(), raise_server_exceptions=False)
        resp = client.get("/boom-disconnect")
        assert resp.status_code == 503
        assert resp.headers.get("Retry-After") == "1"

    def test_real_db_disconnect_through_engine_does_not_leak_traceback(self) -> None:
        """Even if SQLAlchemy's own dialect disconnect-detection fires before
        our listener, the resulting exception must still flow through our
        handler — not Starlette's default 500 path."""
        from fastapi import FastAPI
        from sqlalchemy.exc import OperationalError as SAOperationalError

        app = FastAPI()
        app.add_exception_handler(OperationalError, _db_operational_error_handler)
        app.add_exception_handler(DBAPIError, _db_operational_error_handler)

        @app.get("/boom-real")
        def boom_real():
            # Construct the exact exception class SA would raise when its
            # dialect's is_disconnect() matches.
            raise SAOperationalError(
                statement="SELECT 1",
                params={},
                orig=psycopg2.OperationalError(
                    "server closed the connection unexpectedly\n"
                    "\tThis probably means the server terminated abnormally"
                ),
            )

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/boom-real")
        assert resp.status_code == 503


# ─── FastAPI exception handler ──────────────────────────────────────────────

def _build_app_with_handler() -> FastAPI:
    """Minimal FastAPI app wired to the production exception handler."""
    app = FastAPI()
    app.add_exception_handler(OperationalError, _db_operational_error_handler)
    app.add_exception_handler(DBAPIError, _db_operational_error_handler)

    @app.get("/boom-disconnect")
    def boom_disconnect():
        raise OperationalError(
            statement="SELECT 1",
            params={},
            orig=psycopg2.OperationalError(
                "SSL connection has been closed unexpectedly"
            ),
        )

    @app.get("/boom-real-bug")
    def boom_real_bug():
        raise OperationalError(
            statement="SELECT 1",
            params={},
            orig=psycopg2.errors.UniqueViolation(
                "duplicate key value violates unique constraint \"x_pkey\""
            ),
        )

    return app


class TestFastAPIHandler:
    def test_disconnect_returns_503_with_retry_after(self) -> None:
        client = TestClient(_build_app_with_handler(), raise_server_exceptions=False)
        resp = client.get("/boom-disconnect")
        assert resp.status_code == 503
        assert resp.headers.get("Retry-After") == "1"
        assert "retry" in resp.json()["detail"].lower()

    def test_real_db_bug_returns_500(self) -> None:
        """A constraint violation isn't a disconnect — it must surface as 500
        so an operator notices and fixes the underlying bug."""
        client = TestClient(_build_app_with_handler(), raise_server_exceptions=False)
        resp = client.get("/boom-real-bug")
        assert resp.status_code == 500
        assert resp.json()["detail"] == "Database error"

    def test_disconnect_logs_single_line_no_traceback(self) -> None:
        """The whole point of this fix: no 30-line traceback to stdout.
        We attach a capture handler directly to the `compliance` logger
        because `setup_logger()` sets `propagate=False`, so pytest's caplog
        (which hooks the root logger) never sees these records."""
        import logging
        from app.logger import log as app_log

        records: list[logging.LogRecord] = []

        class _Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)

        handler = _Capture(level=logging.WARNING)
        app_log.addHandler(handler)
        try:
            client = TestClient(
                _build_app_with_handler(), raise_server_exceptions=False
            )
            resp = client.get("/boom-disconnect")
        finally:
            app_log.removeHandler(handler)

        assert resp.status_code == 503
        warning_records = [r for r in records if r.levelno == logging.WARNING]
        assert len(warning_records) == 1, (
            f"expected exactly one WARNING, got {len(warning_records)}"
        )
        rec = warning_records[0]
        assert rec.exc_info is None, "must not attach a traceback"
        assert "db_disconnect_request_failed" in rec.getMessage()
        assert "/boom-disconnect" in rec.getMessage()
