"""Unit tests for pgvector semantic search (Phase J Task 29).

These tests don't hit a real Postgres — they mock the OpenAI embeddings
client and the SQLAlchemy session. The goal is to verify:
  1. embed_text returns a 1536-long list from OpenAI's response.
  2. embed_text swallows OpenAI exceptions and returns None.
  3. get_similar_learnings builds the right cosine-distance SQL.

Real pgvector behaviour (the <=> operator, ivfflat index ordering) is
exercised against Supabase in E2E runs, not here — SQLite has no vector
type and Alembic migrations are tested separately.
"""
from unittest.mock import MagicMock

import pytest

pytest.importorskip("pgvector")  # skip whole module if pgvector isn't installed


def _mock_openai_response(embedding: list[float]) -> MagicMock:
    """Shape a MagicMock to look like `client.embeddings.create(...).data[0].embedding`."""
    resp = MagicMock()
    resp.data = [MagicMock(embedding=embedding)]
    return resp


# ─── embed_text ──────────────────────────────────────────────────────────

def test_embed_text_returns_vector(monkeypatch):
    """Happy path: OpenAI returns 1536 floats, we pass them through."""
    from app.agent import feedback

    fake_vec = [0.001 * i for i in range(1536)]
    fake_client = MagicMock()
    fake_client.embeddings.create.return_value = _mock_openai_response(fake_vec)
    monkeypatch.setattr(feedback, "_emb_client_get", lambda: fake_client)

    result = feedback.embed_text("agent used vague broker language")

    assert isinstance(result, list)
    assert len(result) == 1536
    # Calling args should include the OpenRouter-prefixed model id + text.
    # 2026-05-27 wave-18 — embeddings are routed through OpenRouter
    # (see app/agent/feedback.py:embed_text) which requires the
    # `openai/` prefix on OpenAI-hosted model ids. The test was
    # asserting the bare `text-embedding-3-small` form which has been
    # stale since the OpenRouter cutover; CI has been red since then.
    call = fake_client.embeddings.create.call_args
    assert call.kwargs["model"] == "openai/text-embedding-3-small"
    assert call.kwargs["input"] == "agent used vague broker language"


def test_embed_text_handles_openai_failure_gracefully(monkeypatch):
    """If OpenAI raises, we return None — row still gets written with NULL embedding."""
    from app.agent import feedback

    fake_client = MagicMock()
    fake_client.embeddings.create.side_effect = RuntimeError("rate limited")
    monkeypatch.setattr(feedback, "_emb_client_get", lambda: fake_client)

    assert feedback.embed_text("anything") is None


def test_embed_text_returns_none_for_empty_input():
    """No API call for empty/whitespace — saves quota."""
    from app.agent import feedback

    assert feedback.embed_text("") is None
    assert feedback.embed_text("   \n\t  ") is None


# ─── get_similar_learnings ──────────────────────────────────────────────

def test_get_similar_learnings_builds_cosine_query(monkeypatch):
    """Verify the tool passes the embedding as :q and uses the cosine SQL."""
    from app.agent import feedback, tool_handlers

    # Stub embed_text so we don't call OpenAI.
    stub_emb = [0.5] * 1536
    monkeypatch.setattr(feedback, "embed_text", lambda t: stub_emb)

    # Capture what gets executed.
    fake_row = MagicMock()
    fake_row._mapping = {
        "id": "row-1",
        "supplier": "E.ON Next",
        "checkpoint_name": "Agent confirms DOB",
        "pattern": "vague pattern",
        "agent_verdict": "pass",
        "human_verdict": "fail",
        "lesson": "be explicit",
        "similarity": 0.87,
    }
    fake_result = MagicMock()
    fake_result.fetchall.return_value = [fake_row]
    fake_db = MagicMock()
    fake_db.execute.return_value = fake_result

    ctx = tool_handlers.ToolContext(
        transcript="t",
        word_data=[],
        supplier="E.ON Next",
        agent_speaker_label="A",
        customer_speaker_label="B",
        db=fake_db,
    )

    result = tool_handlers.get_similar_learnings(
        ctx, query="vague broker language", limit=5,
    )

    # Verify the SQL was invoked with the right params.
    fake_db.execute.assert_called_once()
    args, _ = fake_db.execute.call_args
    sql_obj, params = args
    sql_str = str(sql_obj)

    assert "embedding <=> CAST(:q AS vector)" in sql_str
    assert "1 - (embedding <=> CAST(:q AS vector)) AS similarity" in sql_str
    assert "WHERE embedding IS NOT NULL" in sql_str
    assert params["q"] == str(stub_emb)
    assert params["limit"] == 5

    # Result shape.
    assert result["verified"] is True
    assert result["count"] == 1
    assert result["learnings"][0]["similarity"] == pytest.approx(0.87)
    assert result["learnings"][0]["lesson"] == "be explicit"


def test_get_similar_learnings_falls_back_to_legacy_on_sqlite(monkeypatch):
    """SQLite has no vector type → SQLAlchemyError → legacy supplier+cp filter."""
    from sqlalchemy.exc import SQLAlchemyError

    from app.agent import feedback, tool_handlers

    monkeypatch.setattr(feedback, "embed_text", lambda t: [0.1] * 1536)

    fake_db = MagicMock()
    fake_db.execute.side_effect = SQLAlchemyError("no vector type")

    # The legacy path goes through ctx.db.query — mock a chained call that
    # terminates in .all() returning an empty list.
    fake_query = MagicMock()
    fake_query.filter.return_value = fake_query
    fake_query.order_by.return_value = fake_query
    fake_query.limit.return_value = fake_query
    fake_query.all.return_value = []
    fake_db.query.return_value = fake_query

    ctx = tool_handlers.ToolContext(
        transcript="t",
        word_data=[],
        supplier="E.ON Next",
        agent_speaker_label="A",
        customer_speaker_label="B",
        db=fake_db,
    )

    result = tool_handlers.get_similar_learnings(
        ctx,
        query="something",
        supplier="E.ON Next",
        checkpoint_name="Agent confirms DOB",
    )
    fake_db.rollback.assert_called_once()
    assert result["verified"] is True
    assert result["count"] == 0


def test_get_similar_learnings_returns_error_when_no_db():
    from app.agent import tool_handlers

    ctx = tool_handlers.ToolContext(
        transcript="t",
        word_data=[],
        supplier="E.ON Next",
        agent_speaker_label="A",
        customer_speaker_label="B",
        db=None,
    )
    r = tool_handlers.get_similar_learnings(ctx, query="anything")
    assert r["count"] == 0
    assert "no db" in r["error"].lower()
