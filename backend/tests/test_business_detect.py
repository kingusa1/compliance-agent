import pytest
from unittest.mock import AsyncMock, patch

from sqlalchemy.orm import Session

from app.business_detect import detect_business_name, fuzzy_match_customer
from app.models import Customer


@pytest.mark.asyncio
async def test_detect_business_name_extracts_business_from_opening():
    transcript = (
        "Hi, am I speaking with the gas account holder for Evangelical Church? "
        "I'm calling about your renewal with E.ON Next."
    )
    with patch("app.business_detect._call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = "Evangelical Church"
        name = await detect_business_name(transcript)
    assert name == "Evangelical Church"


@pytest.mark.asyncio
async def test_detect_business_name_returns_none_on_no_business():
    with patch("app.business_detect._call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = "Unknown"
        name = await detect_business_name("hello hi yes mate")
    assert name is None


@pytest.mark.asyncio
async def test_detect_business_name_swallows_llm_failure():
    with patch("app.business_detect._call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.side_effect = RuntimeError("openrouter 500")
        name = await detect_business_name("hello")
    assert name is None


# ── A2: fuzzy_match_customer ────────────────────────────────────────────
#
# Implementation note: the plan called for a pg_trgm `similarity()` query,
# but pg_trgm is unavailable on the Supabase pooler (see
# alembic/versions/f1a2b3c4d5e6_complete_call_record.py header comment) and
# the test suite is SQLite-backed via the shared `test_db` fixture in
# conftest.py. We therefore use difflib.SequenceMatcher on the Python side
# — same pattern as app.verification.fuzzy_match — which works identically
# in SQLite tests and Postgres production. Local fixture alias keeps the
# spec-style `db_session` parameter name from the plan.

@pytest.fixture
def db_session(test_db) -> Session:
    return test_db


def test_fuzzy_match_customer_finds_high_similarity(db_session):
    db_session.add(Customer(legal_name="Evangelical Church", slug="evangelical-church"))
    db_session.add(Customer(legal_name="St Peters Church", slug="st-peters-church"))
    db_session.commit()

    match = fuzzy_match_customer("evangelical church", db_session, threshold=0.6)
    assert match is not None
    assert match.legal_name == "Evangelical Church"


def test_fuzzy_match_customer_returns_none_below_threshold(db_session):
    db_session.add(Customer(legal_name="Crosby Grange Properties", slug="crosby"))
    db_session.commit()
    match = fuzzy_match_customer("Hanif Motors", db_session, threshold=0.6)
    assert match is None


def test_fuzzy_match_customer_returns_none_on_empty(db_session):
    assert fuzzy_match_customer("", db_session) is None
    assert fuzzy_match_customer(None, db_session) is None
