"""AI-based deal matching — backstop for the heuristic merge in pipeline.py.

The heuristic merge in ``_maybe_merge_into_existing_deal`` handles obvious
cases cheaply (exact match, substring containment, trailing-tokens, phonetic
uplift, leading-word prefix promotion). When the heuristics return no
match BUT there are still candidates within a reasonable similarity band,
this module asks Opus 4.7 to judge whether any candidate refers to the
same underlying business.

The agent receives:
    - the detected business name on the new call
    - the per-call transcript excerpt (so it can read context like
      "this LOA is for X" or "calling on behalf of Y")
    - the supplier
    - the list of candidate (deal_id, customer_name, supplier) tuples

It returns either a deal_id (matched) or None (create a new deal).

Cost guardrails:
    - Only called when heuristics return None AND at least one candidate
      cleared a low-similarity gate (>= 0.3 SequenceMatcher). No call when
      there's literally nothing in the customer table that could match.
    - Capped at ``MAX_CANDIDATES`` per call (default 8). The top-similarity
      heuristic shortlist is sent to the LLM, not the entire deal table.
    - In-memory result cache keyed on (target, sorted candidate ids) so
      retries / re-analysis don't re-burn the LLM for the same input.

Per the project's model-routing rule (Mohamed mandate 2026-05-16):
    use Opus 4.7 for any decision that affects customer-facing data.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Iterable

from app.analysis import _call_llm
from app.logger import log


# Tunable shortlist size sent to the LLM. The aggregator picks the top N
# similarity candidates from the full candidate set; the LLM picks among
# those. 8 keeps the prompt under ~1k tokens (deal names are short).
MAX_CANDIDATES = 8


@dataclass(frozen=True)
class DealCandidate:
    """Lightweight projection of a CustomerDeal row passed to the AI judge."""
    deal_id: str
    customer_name: str
    supplier: str | None
    similarity: float  # 0..1, the heuristic SequenceMatcher score


# Simple in-memory cache. Bounded by ``_CACHE_MAX`` to avoid unbounded
# growth across a long-running worker. Stale entries fall out FIFO.
_CACHE: dict[str, str | None] = {}
_CACHE_ORDER: list[str] = []
_CACHE_MAX = 512


def _cache_key(target: str, candidates: Iterable[DealCandidate]) -> str:
    """Stable hash of (normalised target, sorted candidate IDs).

    Sorted IDs make the key order-independent. Target is lowercased and
    whitespace-stripped so re-runs with cosmetic differences hit cache.
    """
    norm_target = " ".join(target.lower().split())
    cand_ids = sorted(c.deal_id for c in candidates)
    raw = f"{norm_target}|{','.join(cand_ids)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _cache_get(key: str) -> tuple[bool, str | None]:
    """Return (hit, value). Hit=True means we found a cached answer (which
    may be None for the no-match case); hit=False means cache miss.
    """
    if key in _CACHE:
        return True, _CACHE[key]
    return False, None


def _cache_put(key: str, value: str | None) -> None:
    if key not in _CACHE and len(_CACHE_ORDER) >= _CACHE_MAX:
        # Evict the oldest.
        oldest = _CACHE_ORDER.pop(0)
        _CACHE.pop(oldest, None)
    _CACHE[key] = value
    if key not in _CACHE_ORDER:
        _CACHE_ORDER.append(key)


_PROMPT_SYSTEM = """You are the deal-matching judge for Watt Utilities' compliance system.
Watt brokers UK gas and electricity contracts for small-to-mid businesses
(SMEs). Every recorded sales call refers to ONE underlying business
("the customer"). Many physical businesses generate several recordings
across the lead-gen, pre-sales, verbal-contract, and LOA stages — those
recordings SHOULD all attach to the same deal record.

Your job: decide whether the new call's business is the same as any of
the candidate deals already in the system.

You will see candidate names that may be partial / wrong because the
prior calls' transcripts only mentioned a fragment (the receptionist's
first name, a witness's name, a partial trading-as). Use the call's
transcript excerpt to ground your judgement.

Output rules:
1. Reply with ONLY a JSON object on a single line, no prose, no code
   fences, no markdown.
2. Schema: {"match": "<deal_id>"|"none", "confidence": "high"|"medium"|"low", "reason": "<one short sentence>"}
3. Prefer "none" when uncertain. The cost of a wrong merge (two distinct
   customers collapsed) is HIGHER than the cost of a missed merge (one
   customer split across two deals — humans can merge later).
4. If multiple candidates plausibly match, pick the one most likely to
   be the same physical business; tiebreak by supplier-match.

Confidence calibration:
- "high" = name + supplier + transcript context all line up
- "medium" = name OR supplier strongly supports the match, with one
  weak signal supporting
- "low" = the match relies on a single weak signal; default to "none"
  in this case unless you're confident
"""


_PROMPT_USER_TEMPLATE = """NEW CALL DETAILS
Business name (detected): {target_name}
Supplier (detected):      {target_supplier}

Transcript excerpt (first ~700 words of the call):
---
{transcript_excerpt}
---

CANDIDATE DEALS (consider each, pick at most one)
{candidate_block}

Decision (JSON object only):"""


def _format_candidate_block(candidates: list[DealCandidate]) -> str:
    """Render the candidate list as a tight block for the prompt."""
    lines: list[str] = []
    for i, c in enumerate(candidates, start=1):
        supplier = c.supplier or "(unknown supplier)"
        lines.append(
            f"{i}. deal_id={c.deal_id} | customer_name={c.customer_name!r} "
            f"| supplier={supplier!r} | name_similarity={c.similarity:.2f}"
        )
    return "\n".join(lines)


def _parse_response(raw: str, candidate_ids: set[str]) -> str | None:
    """Parse the LLM's JSON reply. Defensive against extra prose / fences."""
    text = raw.strip()
    # Strip code fences if the model added them despite instructions.
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
    # Find the first { ... } if there's leading prose.
    if not text.startswith("{"):
        start = text.find("{")
        if start == -1:
            return None
        text = text[start:]
    end = text.rfind("}")
    if end != -1:
        text = text[: end + 1]

    try:
        body = json.loads(text)
    except Exception:
        log.warning(f"\U0001f916 DEAL_MATCH_AI bad_json: {raw[:200]!r}")
        return None

    match = body.get("match")
    confidence = body.get("confidence", "low")
    reason = body.get("reason", "")
    if match == "none" or not match:
        log.info(f"\U0001f916 DEAL_MATCH_AI → none ({confidence}): {reason}")
        return None
    if match not in candidate_ids:
        log.warning(
            f"\U0001f916 DEAL_MATCH_AI returned unknown deal_id {match!r}, "
            f"candidates were {candidate_ids}"
        )
        return None
    if confidence == "low":
        log.info(f"\U0001f916 DEAL_MATCH_AI low confidence on {match} — treating as none")
        return None
    log.info(
        f"\U0001f916 DEAL_MATCH_AI → {match} ({confidence}): {reason}"
    )
    return match


async def ai_match_deal(
    target_name: str,
    target_supplier: str | None,
    transcript_excerpt: str,
    candidates: list[DealCandidate],
    *,
    timeout: float = 30.0,
) -> str | None:
    """Ask the LLM judge which (if any) candidate deal is the same business.

    Returns the matched deal_id, or None when the model can't decide.

    Failures (LLM error, bad JSON, unknown ID) all degrade to None — the
    pipeline falls back to creating a new deal, which a reviewer can
    merge manually. The agent never throws.
    """
    if not target_name or not target_name.strip():
        return None
    if not candidates:
        return None

    # Trim candidate list to the top-similarity N.
    shortlist = sorted(candidates, key=lambda c: c.similarity, reverse=True)[:MAX_CANDIDATES]
    candidate_ids = {c.deal_id for c in shortlist}

    cache_key = _cache_key(target_name, shortlist)
    hit, cached = _cache_get(cache_key)
    if hit:
        log.info(f"\U0001f916 DEAL_MATCH_AI cache_hit → {cached!r}")
        return cached

    # Cap the transcript excerpt so the prompt stays affordable. ~700 words
    # is enough context for the business-name signal to land without
    # blowing up the token bill.
    excerpt_words = (transcript_excerpt or "").split()
    excerpt_truncated = " ".join(excerpt_words[:700]) or "(no transcript available)"

    user_prompt = _PROMPT_USER_TEMPLATE.format(
        target_name=target_name.strip(),
        target_supplier=(target_supplier or "(unknown)").strip(),
        transcript_excerpt=excerpt_truncated,
        candidate_block=_format_candidate_block(shortlist),
    )

    try:
        raw = await _call_llm(
            user_prompt,
            timeout=timeout,
            system=_PROMPT_SYSTEM,
            cheap=False,  # Opus 4.7 — accuracy matters here
        )
    except Exception as e:
        log.warning(f"\U0001f916 DEAL_MATCH_AI llm_error: {e}")
        _cache_put(cache_key, None)
        return None

    matched = _parse_response(raw, candidate_ids)
    _cache_put(cache_key, matched)
    return matched
