"""Agent tool handlers. Each tool is a pure function over ToolContext.

These are the *implementations*. The OpenAI-format schemas and dispatcher
live in tools.py. Keeping them separate makes handlers unit-testable
without needing the dispatcher.
"""
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import AgentLearning
from app.verification import fuzzy_match


def _invalid_str(val, field: str) -> dict | None:
    """Return an error dict if val is not a usable string, else None."""
    if not isinstance(val, str) or not val.strip():
        return {"error": f"invalid {field}: expected non-empty string, got {type(val).__name__}"}
    return None


def _norm_token(t: str) -> str:
    """Lowercase, remove all non-alphanumeric chars (including apostrophes, hyphens, quotes)."""
    return re.sub(r"[^a-z0-9]", "", t.lower())


@dataclass
class ToolContext:
    """Immutable per-batch context passed to every tool call."""
    transcript: str
    word_data: list[dict]  # [{word, speaker, start, end, confidence}, ...]
    supplier: str
    agent_speaker_label: str  # e.g. "A" or 0 — AssemblyAI label for the agent
    customer_speaker_label: str
    db: Session | None = None
    # Optional — when set, run_agent_on_batch persists AgentTrace rows tagged
    # with this call_id so the reviewer UI can show the reasoning chain.
    # Left None for unit tests that don't care about tracing.
    call_id: str | None = None


# ─── find_evidence ────────────────────────────────────────────────────────

def find_evidence(ctx: ToolContext, *, query: str) -> dict[str, Any]:
    """Fuzzy-match a query against the transcript. Returns best match + position."""
    if (err := _invalid_str(query, "query")):
        return err
    result = fuzzy_match(ctx.transcript, query, threshold=0.75)
    return {
        "verified": result["verified"],
        "similarity": result["similarity"],
        "best_match": result["best_match"],
    }


# ─── verify_quote ─────────────────────────────────────────────────────────

def verify_quote(ctx: ToolContext, *, quote: str) -> dict[str, Any]:
    """Exact case-insensitive substring check. Stricter than find_evidence."""
    if (err := _invalid_str(quote, "quote")):
        return err
    t = " ".join(ctx.transcript.lower().split())
    q = " ".join(quote.lower().split())
    hit = q in t
    return {"verified": hit, "exact_match": hit}


# ─── check_speaker ────────────────────────────────────────────────────────

def check_speaker(ctx: ToolContext, *, quote: str, expected: str) -> dict[str, Any]:
    """Verify which speaker said a quote.

    Args:
        quote: The text to locate.
        expected: "Agent" or "Customer".

    Strategy: normalize quote, search word_data for a matching sequence,
    return the speaker label of that window.
    """
    if (err := _invalid_str(quote, "quote")):
        return err
    if (err := _invalid_str(expected, "expected")):
        return err
    if expected not in ("Agent", "Customer"):
        return {"error": f"expected must be 'Agent' or 'Customer', got {expected!r}"}

    if not ctx.word_data:
        return {"verified": False, "speaker": "Unknown", "reason": "no word_data available"}

    quote_tokens = [tok for tok in (_norm_token(t) for t in quote.split()) if tok]
    if not quote_tokens:
        return {"verified": False, "speaker": "Unknown", "reason": "empty quote"}

    words = ctx.word_data
    n = len(quote_tokens)
    best_match_start = -1
    best_match_hits = 0

    for i in range(len(words) - n + 1):
        window = [_norm_token(w["word"]) for w in words[i : i + n]]
        hits = sum(1 for a, b in zip(window, quote_tokens) if a == b)
        if hits > best_match_hits:
            best_match_hits = hits
            best_match_start = i

    # Require at least 2 matching tokens AND 67% hit rate for multi-word quotes.
    # Single-word quotes still require exact match.
    min_required = max(2, (n * 2) // 3) if n >= 3 else n
    if best_match_hits < min_required:
        return {"verified": False, "speaker": "Unknown", "reason": "quote not found in word_data"}

    matched_speaker_label = words[best_match_start].get("speaker")
    if matched_speaker_label is None:
        return {"verified": False, "speaker": "Unknown", "reason": "word_data missing speaker field"}

    if matched_speaker_label == ctx.agent_speaker_label:
        actual = "Agent"
    elif matched_speaker_label == ctx.customer_speaker_label:
        actual = "Customer"
    else:
        actual = f"Speaker {matched_speaker_label}"

    return {
        "verified": actual == expected,
        "speaker": actual,
        "match_ratio": best_match_hits / n,
    }


# ─── get_word_context ─────────────────────────────────────────────────────

def get_word_context(ctx: ToolContext, *, position: float, window_seconds: float = 3.0) -> dict[str, Any]:
    """Pull words around a timestamp. Useful when agent wants to see context."""
    if not isinstance(position, (int, float)):
        return {"error": "position must be numeric"}
    lo = position - window_seconds
    hi = position + window_seconds
    window = [
        {"word": w["word"], "speaker": w.get("speaker"), "start": w.get("start")}
        for w in ctx.word_data
        if lo <= w.get("start", 0) <= hi
    ]
    return {"words": window, "count": len(window)}


# ─── flag_low_confidence ──────────────────────────────────────────────────

def flag_low_confidence(ctx: ToolContext, *, checkpoint: str, reason: str) -> dict[str, Any]:
    """Mark a checkpoint as needing human review. Return is ack only — the
    agent loop consumes this and sets needs_review=True in the final output."""
    if (err := _invalid_str(checkpoint, "checkpoint")):
        return err
    if (err := _invalid_str(reason, "reason")):
        return err
    return {"verified": True, "flagged": True, "checkpoint": checkpoint, "reason": reason}


# ─── get_similar_learnings ────────────────────────────────────────────────

# SQL for pgvector cosine search. The <=> operator is cosine DISTANCE (0=same,
# 2=opposite); `1 - distance` flips it to similarity (1=same, -1=opposite).
# CAST(:q AS vector) is needed because we pass the embedding as a string
# literal (SQLAlchemy has no native binding for the vector type).
_COSINE_SQL = text("""
    SELECT id, supplier, checkpoint_name, pattern, agent_verdict,
           human_verdict, lesson,
           1 - (embedding <=> CAST(:q AS vector)) AS similarity
    FROM agent_learnings
    WHERE embedding IS NOT NULL
    ORDER BY embedding <=> CAST(:q AS vector)
    LIMIT :limit
""")


def get_similar_learnings(
    ctx: ToolContext,
    *,
    query: str | None = None,
    supplier: str | None = None,
    checkpoint_name: str | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """Fetch anonymized past corrections similar to `query` via cosine search.

    Phase J Task 29: primary path is pgvector semantic search over
    `agent_learnings.embedding`. `query` (free-form text) is embedded and used
    to find the nearest `limit` rows by cosine distance.

    Backwards-compat: if `query` is omitted but `supplier` + `checkpoint_name`
    are given (old callers), we build a query string from them and still do
    semantic search. If the DB is SQLite (no pgvector) we fall back to the
    legacy supplier+checkpoint exact match.
    """
    # Normalise limit early.
    if not isinstance(limit, int) or limit < 1:
        limit = 5
    limit = min(limit, 20)

    # Build the query text. At least one of (query, supplier+cp) is required.
    q_text = ""
    if isinstance(query, str) and query.strip():
        q_text = query.strip()
    elif isinstance(supplier, str) and supplier.strip() \
            and isinstance(checkpoint_name, str) and checkpoint_name.strip():
        q_text = f"{supplier.strip()} {checkpoint_name.strip()}"
    else:
        return {"error": "must provide `query` or `supplier` + `checkpoint_name`"}

    if ctx.db is None:
        return {"verified": False, "count": 0, "learnings": [], "error": "no db session"}

    # Try the cosine path. If the DB doesn't have pgvector (SQLite tests) or
    # the embedding call fails, fall back to the legacy supplier+cp filter
    # so existing behaviour isn't lost.
    from app.agent.feedback import embed_text
    q_emb = embed_text(q_text)
    if q_emb is not None:
        try:
            rows = ctx.db.execute(
                _COSINE_SQL,
                {"q": str(q_emb), "limit": limit},
            ).fetchall()
            return {
                "verified": True,
                "count": len(rows),
                "learnings": [
                    {
                        "pattern": r._mapping["pattern"],
                        "lesson": r._mapping["lesson"],
                        "agent_verdict": r._mapping["agent_verdict"],
                        "human_verdict": r._mapping["human_verdict"],
                        "supplier": r._mapping["supplier"],
                        "checkpoint_name": r._mapping["checkpoint_name"],
                        "similarity": float(r._mapping["similarity"]),
                    }
                    for r in rows
                ],
            }
        except SQLAlchemyError:
            # SQLite lacks `vector` type / `<=>` operator → fall through to
            # legacy path. Production Postgres with pgvector won't raise here.
            ctx.db.rollback()

    # ─── Fallback: legacy supplier+checkpoint exact match ────────────────
    # Used when (a) embedding failed, or (b) DB is SQLite (unit tests).
    if not (isinstance(supplier, str) and supplier.strip()
            and isinstance(checkpoint_name, str) and checkpoint_name.strip()):
        return {"verified": False, "count": 0, "learnings": [],
                "error": "embedding unavailable and no supplier/checkpoint fallback given"}

    rows = (
        ctx.db.query(AgentLearning)
        .filter(
            AgentLearning.supplier == supplier,
            AgentLearning.checkpoint_name == checkpoint_name,
        )
        .order_by(AgentLearning.created_at.desc())
        .limit(limit)
        .all()
    )
    return {
        "verified": True,
        "count": len(rows),
        "learnings": [
            {
                "pattern": r.pattern,
                "lesson": r.lesson,
                "agent_verdict": r.agent_verdict,
                "human_verdict": r.human_verdict,
            }
            for r in rows
        ],
    }
