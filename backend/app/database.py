from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings

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
