import logging

from sqlalchemy import create_engine, event
from sqlalchemy.exc import DisconnectionError
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings

log = logging.getLogger("compliance.database")

# Substrings that mean "the TCP/TLS pipe to Postgres is gone, this connection
# can never recover". Matched against the lowered str() of the raised exception.
_DISCONNECT_SIGNATURES = (
    "ssl connection has been closed unexpectedly",
    "server closed the connection unexpectedly",
    "connection already closed",
    "terminating connection due to administrator command",
    "could not receive data from server",
    "could not send data to server",
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
        "options": "-c statement_timeout=15000",
    },
)


@event.listens_for(engine, "handle_error")
def _handle_disconnect(ctx) -> None:
    """Detect mid-query connection drops and recycle the dead connection.

    pool_pre_ping catches stale connections BEFORE a query runs. This handles
    the other case: the connection is killed WHILE the query is in flight
    (Supavisor restart, IPv6 RST, k8s pod recycle). Without this hook the
    same dead psycopg2 connection can be returned to the pool and reused on
    the next request, multiplying one network blip into dozens of failures.
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
    # Re-raise as DisconnectionError so SQLAlchemy invalidates the connection
    # AND the whole pool's "ping" generation, forcing the next checkout to
    # open a brand-new TCP/TLS session instead of reusing the dead one.
    raise DisconnectionError(str(orig)) from orig


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
