"""Business-name detection. Distinct from detect_names (which finds the
agent/customer pair of people) — extracts the non-person entity (the
business the call is about). Used downstream by pipeline.detect_metadata
to fuzzy-merge auto-detect uploads onto existing Customer rows."""
from __future__ import annotations

from difflib import SequenceMatcher

from sqlalchemy.orm import Session

from app.analysis import _call_llm
from app.logger import log
from app.models import Customer


_PROMPT = """Read the opening of this energy brokerage call. Extract the
NAME OF THE BUSINESS that is the customer of the call (NOT the person
speaking, NOT the supplier, NOT the broker company). Examples of valid
output: "Evangelical Church", "Crosby Grange Properties", "St Peters
Church". If unclear, output exactly: Unknown.

Transcript:
{transcript_start}

Respond with ONLY the business name on a single line, no JSON, no prose."""


async def detect_business_name(transcript: str) -> str | None:
    """Return the business name spoken in the call, or None if unclear.

    Failures are swallowed and return None — never propagate to the caller.
    The pipeline keeps running with no business name and the auto-detect
    stub deal stays as fallback.
    """
    words = transcript.split()
    transcript_start = " ".join(words[:600])
    prompt = _PROMPT.replace("{transcript_start}", transcript_start)
    try:
        result = await _call_llm(prompt, timeout=20.0)
    except Exception as e:
        log.warning(f"\U0001f3e2 BUSINESS_DETECT failed: {e}")
        return None
    name = result.strip().strip('"').strip()
    if not name or name == "Unknown":
        return None
    log.info(f"\U0001f3e2 BUSINESS_DETECT → {name!r}")
    return name


def fuzzy_match_customer(
    name: str | None,
    db: Session,
    threshold: float = 0.6,
) -> Customer | None:
    """Return the highest-similarity Customer.legal_name match, or None
    if nothing clears the threshold.

    Used by pipeline.detect_metadata to collapse stub deals onto existing
    Customer rows when the AI-detected business name resembles a stored
    legal_name.

    Implementation note: the original sprint plan called for pg_trgm
    ``similarity()``, but that extension is unavailable on the Supabase
    pooler (see migration f1a2b3c4d5e6 header — "fuzzy search uses ILIKE
    instead"). We score on the Python side via ``difflib.SequenceMatcher``
    — same approach used in ``app.verification.fuzzy_match`` — which works
    in both SQLite tests and Postgres production with no extension
    dependency. n stays small (one customer table scan) so this is fine.
    """
    if not name or not name.strip():
        return None

    target = name.strip().lower()
    best: Customer | None = None
    best_score = 0.0
    for customer in db.query(Customer).all():
        legal = (customer.legal_name or "").lower()
        if not legal:
            continue
        score = SequenceMatcher(None, target, legal).ratio()
        if score >= threshold and score > best_score:
            best = customer
            best_score = score
    return best
