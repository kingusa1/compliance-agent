"""Feedback loop: turn a human review into an anonymized learning row.

When a human reviewer flips a checkpoint verdict, we call a cheap LLM
(Gemini Flash) to extract the *pattern* and *lesson* — PII-free —
then insert into agent_learnings. This row survives per-tenant data
retention and powers the get_similar_learnings agent tool.
"""
import asyncio
import json
import logging

from sqlalchemy.orm import Session

from app.analysis import _call_llm
from app.config import settings
from app.logger import log
from app.models import AgentLearning

logger = logging.getLogger(__name__)


# ─── Embeddings (Phase J Task 29) ──────────────────────────────────────────
# Singleton OpenAI client; lazy-initialized so importing this module doesn't
# require an API key (tests mock _emb_client_get).
_emb_client = None


def _emb_client_get():
    """Lazy OpenAI-compatible client for embeddings.

    2026-05-27 — the production system uses OpenRouter end-to-end (no
    direct OpenAI account). The prior client construction with bare
    ``api_key=settings.openai_api_key`` logged `embedding failed: Missing
    credentials` on every verdict-change feedback path because
    ``OPENAI_API_KEY`` is empty in prod. Switch the client to use
    OpenRouter's OpenAI-compatible endpoint when ``OPENROUTER_API_KEY``
    is set, falling back to direct OpenAI only when an explicit
    ``OPENAI_API_KEY`` is present.

    Tests that monkeypatch ``embed_text`` directly never reach this
    function. Tests that DO want a real client can set either env var.
    """
    global _emb_client
    if _emb_client is None:
        from openai import OpenAI  # imported lazily
        # Prefer explicit OpenAI direct when key is set; otherwise fall
        # back to OpenRouter's openai-compatible endpoint.
        openai_key = getattr(settings, "openai_api_key", None) or None
        openrouter_key = getattr(settings, "openrouter_api_key", None) or None
        if openai_key:
            _emb_client = OpenAI(api_key=openai_key, timeout=10.0)
        elif openrouter_key:
            _emb_client = OpenAI(
                api_key=openrouter_key,
                base_url="https://openrouter.ai/api/v1",
                timeout=10.0,
            )
        else:
            # Neither key set — leave client unset so `embed_text` short-
            # circuits to None instead of constructing a credential-less
            # client that would log "Missing credentials" on every call.
            return None
    return _emb_client


def embed_text(text: str) -> list[float] | None:
    """Return a 1536-dim embedding for `text` via text-embedding-3-small.

    Returns None for empty/whitespace input, missing credentials, or any
    SDK / network error — the caller must tolerate None (the learning row
    is still written with embedding=NULL so a future backfill can retry).

    2026-05-27 — short-circuits to None when neither OPENAI_API_KEY nor
    OPENROUTER_API_KEY is set, instead of letting the OpenAI SDK raise
    its noisy "Missing credentials" error. Owner-reported.
    """
    if not text or not text.strip():
        return None
    client = _emb_client_get()
    if client is None:
        return None
    try:
        # text-embedding-3-small is supported by OpenRouter (mirrored from
        # OpenAI) and by OpenAI direct. Same model id either way.
        r = client.embeddings.create(
            model="openai/text-embedding-3-small",
            input=text[:8000],
            timeout=10.0,
        )
        return r.data[0].embedding
    except Exception as e:
        logger.warning("embedding failed: %s", e)
        return None


ABSTRACTION_PROMPT = """You convert a human reviewer's correction into an ANONYMIZED lesson.

The raw input contains specific quotes and reviewer notes. Your output MUST:
1. Omit ALL proper nouns (customer names, agent names, addresses, companies)
2. Omit ALL specific dates, phone numbers, amounts, meter numbers
3. Keep ONLY the abstract pattern — what the agent did wrong in category terms
4. Keep ONLY the general lesson — a rule the AI can apply to other calls

Input:
  Supplier: {supplier}
  Checkpoint: {checkpoint_name}
  Agent verdict: {agent_verdict}
  Human verdict: {human_verdict}
  Reviewer notes: {reviewer_notes}
  Transcript excerpt (DO NOT COPY INTO OUTPUT): {excerpt}

Return ONLY this JSON (no prose):
{{
  "pattern": "short abstract description of the category of error (no names, no specifics)",
  "lesson": "general rule this teaches the AI (no names, no specifics)"
}}"""


async def abstract_and_store_review(
    *,
    db: Session | None = None,  # noqa: ARG001 — back-compat, wave-18 ignores
    supplier: str,
    checkpoint_name: str,
    transcript_excerpt: str,
    agent_verdict: str,
    human_verdict: str,
    reviewer_notes: str | None,
) -> None:
    """Extract anonymized pattern via Gemini Flash, write AgentLearning row.

    Safe: failures are logged and swallowed — never propagates to the caller
    (the review API endpoint should not fail just because feedback processing failed).

    Wave-18 (2026-05-27): the persisted INSERT is now executed inside a
    worker thread via `asyncio.to_thread` so the event loop is never
    blocked by the embedding-vector commit or any Supavisor SSL-reconnect
    retry. The `db` keyword argument is preserved for back-compat with
    callers in routes.py + hitl_routes.py but is INTENTIONALLY IGNORED —
    the worker opens its own per-thread SessionLocal.
    """
    if agent_verdict == human_verdict:
        # No correction — no lesson
        return

    # `db` kwarg is back-compat-only; wave-18 writes through a per-thread
    # session inside the threadpool worker below.
    del db

    prompt = ABSTRACTION_PROMPT.format(
        supplier=supplier,
        checkpoint_name=checkpoint_name,
        agent_verdict=agent_verdict,
        human_verdict=human_verdict,
        reviewer_notes=reviewer_notes or "(no notes)",
        excerpt=transcript_excerpt[:2000],
    )

    try:
        raw = await _call_llm(prompt, timeout=30.0)
    except Exception as e:
        logger.warning("feedback abstraction LLM call failed: %s", e)
        return

    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("\n", 1)
        if len(parts) > 1:
            text = parts[1].rsplit("```", 1)[0].strip()
        else:
            import re as _re
            text = _re.sub(r"^```\w*", "", parts[0]).rsplit("```", 1)[0].strip()

    try:
        parsed = json.loads(text)
        pattern = parsed.get("pattern", "").strip()
        lesson = parsed.get("lesson", "").strip()
    except (json.JSONDecodeError, AttributeError) as e:
        logger.warning("feedback abstraction returned non-JSON: %s", e)
        return

    if not pattern or not lesson:
        logger.warning("feedback abstraction missing pattern or lesson: %s", parsed)
        return

    # Embed the pattern (Phase J Task 29) — failure returns None, row still
    # gets written so the lesson isn't lost. Backfill script can retry later.
    try:
        pattern_embedding = await asyncio.to_thread(embed_text, pattern)
    except Exception:
        pattern_embedding = None

    # Wave-18 (2026-05-27, perf P0) — MOVE SYNC DB WRITE OFF THE ASYNCIO LOOP.
    # The prior implementation called `db.add(row); db.commit()` directly on
    # the loop thread. With 29KB embedding vectors going into Postgres
    # through Supavisor's SSL-fragile transaction pooler the commit could
    # block for seconds while psycopg2 waited on a half-closed socket.
    # Concurrent REVIEW activity accumulated those blocked seconds into the
    # production 184-second `loop_lag_canary actual=184147ms` freeze
    # observed at 2026-05-27 11:05 UTC. The worker now opens its OWN
    # SessionLocal so neither the INSERT nor any Supavisor reconnect
    # retries can reach the event loop. The legacy `db` parameter is
    # accepted for back-compat but intentionally ignored.
    def _persist_learning_in_thread() -> bool:
        from app.database import SessionLocal as _SL
        _db = _SL()
        try:
            row = AgentLearning(
                supplier=supplier,
                checkpoint_name=checkpoint_name,
                pattern=pattern,
                agent_verdict=agent_verdict,
                human_verdict=human_verdict,
                lesson=lesson,
                embedding=pattern_embedding,
            )
            _db.add(row)
            _db.commit()
            return True
        except Exception as e:  # noqa: BLE001 — feedback must never crash caller
            try:
                _db.rollback()
            except Exception:  # noqa: BLE001
                pass
            logger.warning(
                "feedback persist (off-loop) failed: %s: %s",
                type(e).__name__, e,
            )
            return False
        finally:
            try:
                _db.close()
            except Exception:  # noqa: BLE001
                pass

    persisted = await asyncio.to_thread(_persist_learning_in_thread)
    if not persisted:
        return

    log.info(
        f"\U0001f4da LEARNING stored supplier=\"{supplier}\" cp=\"{checkpoint_name}\" "
        f"{agent_verdict}\u2192{human_verdict} "
        f"embedding={'yes' if pattern_embedding is not None else 'no'}"
    )
