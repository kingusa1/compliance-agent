"""Agent + customer name canonicaliser.

Deepgram Nova-3 is excellent for English but agents' names get transcribed
inconsistently across calls (Alex Fitton / Alex Pitton / Alex Mitton,
Parat / Paras, Afak / Afaq). Each per-call detect_names() returns the
transcribed spelling, so the agents page ends up showing 5 different
spellings of the same person.

This module fuzzy-matches a raw name against the canonical names already
seen in the DB. If a match is found above the similarity threshold AND
the canonical version is more frequent (or first alphabetically when
tied), we return the canonical spelling. Otherwise we return the raw
name and let it become a new canonical entry on its own.

The function is intentionally cheap: it loads the distinct agent_name set
from the calls table (a few dozen rows at most), runs difflib in pure
Python, and returns in <5ms.

Wired into pipeline._step_detect_metadata right after detect_names().
"""
from __future__ import annotations

import logging
from collections import Counter
from difflib import SequenceMatcher
from typing import Iterable

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


def _similarity(a: str, b: str) -> float:
    """Case-insensitive SequenceMatcher ratio, 0.0–1.0."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def canonicalise(
    raw_name: str | None,
    known: Iterable[str],
    threshold: float = 0.84,
) -> tuple[str | None, bool]:
    """Return (canonical_name, was_normalised).

    If ``raw_name`` is similar enough to a known name (above ``threshold``),
    return the known name. Tie-breaker: pick the longest known name (assumes
    longer spelling is more likely correct — "Christopher" beats "Christ").

    With no candidates above threshold, return ``raw_name`` unchanged.
    """
    if not raw_name or raw_name.strip() == "":
        return raw_name, False
    raw = raw_name.strip()

    candidates = [k.strip() for k in known if k and k.strip()]
    if not candidates:
        return raw, False

    # If the raw name is exactly one of the known names (case-insensitive),
    # return the canonical-cased version.
    for c in candidates:
        if c.lower() == raw.lower():
            return c, False

    # Score every candidate; keep those above threshold.
    scored = [(c, _similarity(raw, c)) for c in candidates]
    above = [(c, s) for (c, s) in scored if s >= threshold]
    if not above:
        return raw, False

    # Best by similarity, then by length (longer = canonical).
    above.sort(key=lambda x: (-x[1], -len(x[0])))
    chosen = above[0][0]
    return chosen, chosen.lower() != raw.lower()


def known_agent_names(db: Session, *, exclude_call_id: str | None = None) -> list[str]:
    """Distinct, non-empty agent_name values from the calls table.

    The list is small (one entry per real agent), so a full-table SELECT
    is fine. ``exclude_call_id`` keeps the current call's name out of the
    candidate set so a typo doesn't normalise to itself.
    """
    # Lazy-import to keep this module easy to test in isolation.
    from app.models import Call

    q = db.query(Call.agent_name).filter(Call.agent_name.isnot(None), Call.agent_name != "")
    if exclude_call_id:
        q = q.filter(Call.id != exclude_call_id)
    names = [n[0] for n in q.all() if n[0]]
    # Most-frequent first — when tied, the longer spelling wins (length
    # tie-breaker happens in canonicalise()).
    freq = Counter(n.strip() for n in names)
    return [n for n, _ in freq.most_common()]


def canonicalise_agent(
    raw_name: str | None,
    db: Session,
    *,
    exclude_call_id: str | None = None,
    threshold: float = 0.84,
) -> str | None:
    """Top-level helper: pull known names from DB and canonicalise."""
    known = known_agent_names(db, exclude_call_id=exclude_call_id)
    canonical, normalised = canonicalise(raw_name, known, threshold=threshold)
    if normalised:
        log.info(f"\U0001f3f7️ agent name normalised: {raw_name!r} → {canonical!r}")
    return canonical
