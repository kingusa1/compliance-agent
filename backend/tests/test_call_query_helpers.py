"""Tests for `_call_query_helpers.defer_heavy_call_columns`.

Verifies the helper applies `defer()` to the documented heavy columns
without breaking the lightweight read path, and asserts the heavy
columns are actually omitted from the emitted SQL projection.
"""
from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app._call_query_helpers import HEAVY_CALL_COLUMNS, defer_heavy_call_columns
from app.database import Base
from app.models import Call


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def _seed_call(session) -> str:
    cid = str(uuid.uuid4())
    c = Call(
        id=cid,
        filename="t.mp3",
        file_path="/tmp/t.mp3",
        customer_name="X Ltd",
        agent_name="Reviewer",
        score="20/24",
        transcript="lorem ipsum " * 2000,  # ~24KB
        gemini_transcript="g " * 2000,
        assemblyai_transcript="a " * 2000,
        groq_whisper_transcript="q " * 2000,
        cohere_transcript="c " * 2000,
        word_data="[]",
        meta={},
        deepgram_metadata={},
        assemblyai_metadata={},
        openai_whisper_metadata={},
        processing_log={},
        raw_llm_io={},
        draft_snapshot="{}",
        status="completed",
    )
    session.add(c)
    session.commit()
    return cid


def test_helper_emits_sql_without_heavy_columns(session) -> None:
    """The deferred columns must NOT appear in the SELECT projection.
    We compile the query to its literal SQL string and grep for each
    deferred column name — none should be present.

    This is the actual perf-win invariant: every byte of those columns
    that DOESN'T cross the wire is the saving we ship for."""
    _seed_call(session)
    q = defer_heavy_call_columns(session.query(Call)).filter(Call.id.isnot(None))
    sql = str(
        q.statement.compile(
            session.get_bind(), compile_kwargs={"literal_binds": True}
        )
    )
    # Each deferred column name must not appear in the SELECT projection.
    for col in HEAVY_CALL_COLUMNS:
        # The compiled SQL uses `calls.transcript`, `calls.gemini_transcript`,
        # etc. Look for "calls.<col>" specifically to avoid false-positives
        # on a column whose name appears inside a literal.
        pattern = rf"\bcalls\.{re.escape(col)}\b"
        assert not re.search(pattern, sql), (
            f"column {col!r} should be deferred but appeared in projection:\n{sql[:1000]}"
        )


def test_helper_still_lets_us_fetch_lightweight_columns(session) -> None:
    """Smoke test: the row object loads via the deferred query and the
    lightweight columns we DO use in list views are present without
    triggering an extra SELECT."""
    cid = _seed_call(session)
    q = defer_heavy_call_columns(session.query(Call)).filter(Call.id == cid)
    call = q.first()
    assert call is not None
    # Lightweight read — must work with the data we already have in memory.
    assert call.id == cid
    assert call.customer_name == "X Ltd"
    assert call.agent_name == "Reviewer"
    assert call.score == "20/24"
    assert call.status == "completed"


def test_helper_lazy_loads_heavy_column_on_access(session) -> None:
    """Accessing a deferred column triggers a per-attribute SELECT but
    still returns the right value. This is the trade-off: list endpoints
    never touch these attrs (no N+1), but the data is still available
    when an admin truly needs it."""
    cid = _seed_call(session)
    q = defer_heavy_call_columns(session.query(Call)).filter(Call.id == cid)
    call = q.first()
    assert call is not None
    # Lazy-load the transcript; should still have the seeded content.
    assert "lorem" in (call.transcript or "")


def test_heavy_columns_set_matches_documented_list() -> None:
    """Guard: anyone adding to HEAVY_CALL_COLUMNS must keep the docstring
    list at the top of `_call_query_helpers` in sync. If this assertion
    drifts, refresh both ends."""
    expected = {
        "transcript", "gemini_transcript", "assemblyai_transcript",
        "groq_whisper_transcript", "cohere_transcript", "word_data",
        "meta", "deepgram_metadata", "assemblyai_metadata",
        "openai_whisper_metadata", "processing_log", "raw_llm_io",
        "draft_snapshot",
    }
    assert set(HEAVY_CALL_COLUMNS) == expected
