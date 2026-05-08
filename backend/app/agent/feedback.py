"""Feedback loop: turn a human review into an anonymized learning row.

When a human reviewer flips a checkpoint verdict, we call a cheap LLM
(Gemini Flash) to extract the *pattern* and *lesson* — PII-free —
then insert into agent_learnings. This row survives per-tenant data
retention and powers the get_similar_learnings agent tool.
"""
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
    """Lazy OpenAI client. Imported inside the function so the `openai` SDK
    is optional at import time — tests that monkeypatch `embed_text` directly
    never construct a real client."""
    global _emb_client
    if _emb_client is None:
        from openai import OpenAI  # imported lazily
        _emb_client = OpenAI(api_key=settings.openai_api_key)
    return _emb_client


def embed_text(text: str) -> list[float] | None:
    """Return a 1536-dim embedding for `text` via text-embedding-3-small.

    Returns None for empty/whitespace input or if the OpenAI call fails — the
    caller must tolerate None (the learning row is still written with
    embedding=NULL so future backfills can retry).
    """
    if not text or not text.strip():
        return None
    try:
        r = _emb_client_get().embeddings.create(
            model="text-embedding-3-small",
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
    db: Session,
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
    """
    if agent_verdict == human_verdict:
        # No correction — no lesson
        return

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
    import asyncio
    try:
        pattern_embedding = await asyncio.to_thread(embed_text, pattern)
    except Exception:
        pattern_embedding = None

    row = AgentLearning(
        supplier=supplier,
        checkpoint_name=checkpoint_name,
        pattern=pattern,
        agent_verdict=agent_verdict,
        human_verdict=human_verdict,
        lesson=lesson,
        embedding=pattern_embedding,
    )
    db.add(row)
    db.commit()
    log.info(
        f"\U0001f4da LEARNING stored supplier=\"{supplier}\" cp=\"{checkpoint_name}\" "
        f"{agent_verdict}\u2192{human_verdict} "
        f"embedding={'yes' if pattern_embedding is not None else 'no'}"
    )
