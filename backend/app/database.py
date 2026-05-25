import logging

from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings

log = logging.getLogger("compliance.database")

# Substrings that mean "the TCP/TLS pipe to Postgres is gone, this connection
# can never recover". Matched against the lowered str() of the raised exception.
# This is the SINGLE source of truth — main.py imports it from here so the
# engine listener and the FastAPI handler can never drift out of sync.
_DISCONNECT_SIGNATURES = (
    "ssl connection has been closed unexpectedly",
    "server closed the connection unexpectedly",
    "connection already closed",
    "terminating connection due to administrator command",
    "could not receive data from server",
    "could not send data to server",
    # 2026-05-25 — Supavisor (Supabase's pooler) bug seen during deploy
    # cycles: psycopg2 opens the TCP connection, sends `SET CLIENT_ENCODING
    # TO 'UTF8'`, and the pooler returns a ParameterStatus reply that
    # doesn't include `client_encoding`. psycopg2 raises this exact string.
    # The connection is unrecoverable — it must be invalidated and
    # re-opened — so it belongs in the disconnect class, not the generic
    # `db_error` class that goes to Sentry as a real bug.
    "didn't return client encoding",
    # Defensive coverage of more Supavisor / pgbouncer transient errors
    # that surface during deploy cycles or pooler restarts and are
    # unrecoverable on the same connection.
    "consuming input failed",
    "unexpected response from server",
    "no connection to the server",
    "connection has been closed",
)

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    # Sized for Railway Pro (24GB) + Supabase Supavisor transaction pooler.
    # Pro plan has headroom for a larger warm-pool; LIFO checkout keeps the
    # hottest connection at the top so consecutive requests reuse a primed
    # TCP/TLS session and skip the cross-region handshake.
    pool_size=25,
    max_overflow=50,
    pool_recycle=1800,
    pool_use_lifo=True,
    # Default compiled-statement cache is 500; lift to 1200 so the hot
    # query shapes (queue, tracker, calls, deals) stop falling out and
    # paying the parse+plan cost on every request.
    query_cache_size=1200,
    connect_args={
        "connect_timeout": 10,
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 3,
        # 2026-05-25 — Set client_encoding via psycopg2's own arg so the
        # driver never has to round-trip a `SET CLIENT_ENCODING` query
        # against Supavisor at session start. Supavisor under load
        # occasionally returns a ParameterStatus without `client_encoding`,
        # tripping `psycopg2.OperationalError: server didn't return client
        # encoding` for every new connection in the affected window.
        # Passing it at connect time short-circuits the negotiation.
        "client_encoding": "utf8",
        "options": "-c statement_timeout=15000",
    },
)


@event.listens_for(engine, "handle_error")
def _handle_disconnect(ctx) -> None:
    """Detect mid-query connection drops and force pool invalidation.

    pool_pre_ping catches stale connections BEFORE a query runs. This handles
    the other case: the connection is killed WHILE the query is in flight
    (Supavisor restart, IPv6 RST, k8s pod recycle). Without this hook the
    same dead psycopg2 connection can be returned to the pool and reused on
    the next request, multiplying one network blip into dozens of failures.

    CRITICAL — we set `ctx.is_disconnect = True` rather than raising
    DisconnectionError. SQLAlchemy's dispatcher reads that flag in its
    finally block and calls `pool._invalidate(...)`. If we raised, the
    exception class reaching Starlette would be DisconnectionError (which
    is NOT a DBAPIError subclass), the FastAPI handler in main.py would
    never match, and Starlette would dump the 30-line traceback we set
    out to suppress — defeating the whole point of the hook.

    The signature list is also broader than the psycopg2 dialect's built-in
    disconnect detection, so this listener catches edge-case wordings
    (Supavisor variants) the dialect would miss.
    """
    orig = ctx.original_exception
    if orig is None:
        return
    msg = str(orig).lower()
    if not any(sig in msg for sig in _DISCONNECT_SIGNATURES):
        return
    log.warning(
        "db_disconnect_detected",
        extra={"err_type": type(orig).__name__, "err": str(orig).strip()[:240]},
    )
    # SQLAlchemy will: (a) invalidate the dead pool connection in the
    # finally block, (b) wrap `orig` in the dialect's normal
    # OperationalError, (c) let it propagate up to FastAPI where our
    # main.py exception handler converts it to a 503 + one-line warning.
    ctx.is_disconnect = True


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_tables():
    Base.metadata.create_all(bind=engine)
