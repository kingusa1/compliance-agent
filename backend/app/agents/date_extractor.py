"""DateExtractorAgent — pull go-live dates from a transcript.

Two-tier strategy:

1. Cheap regex pre-pass scans for date patterns. If zero candidates,
   we skip the LLM entirely (free).
2. Haiku 4.5 confirms which candidate is actually the contract
   ``expected_live_date`` and which (if any) is the contract end date.
   Returns ISO dates.

Output applies to ``CustomerDeal.expected_live_date``. Stays a no-op
when the transcript doesn't mention dates.
"""
from __future__ import annotations

import json
import re
from datetime import date as _date
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.analysis import _call_llm
from app.logger import log
from app.models import Call, CustomerDeal
from app.resilience import LLM_RETRY


# Cheap regex pre-pass — covers the most common UK date patterns the
# agent / customer use during go-live discussions.
#   - "1st of December"
#   - "December 1, 2026"
#   - "01/12/2026" / "1/12"
#   - "next month", "in 3 weeks", "from 1 January"
_DATE_PATTERNS = [
    r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\b",
    r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s*\d{4})?\b",
    r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b",
    r"\b(?:next|coming)\s+(?:month|week|year)\b",
    r"\bin\s+\d+\s+(?:days?|weeks?|months?)\b",
    r"\bfrom\s+\d{1,2}(?:st|nd|rd|th)?\s+\w+",
    r"\bgo[-\s]?live\b",
    r"\brenewal\b",
]


SYSTEM_PROMPT = """You are the Date Extractor Agent for an energy-broker compliance system.

You read a phone-call transcript and extract any GO-LIVE / contract-start date
the agent and customer agreed on, plus the contract END / expiry date if
mentioned.

Return ONLY valid JSON in this exact shape:

{
  "expected_live_date": "YYYY-MM-DD" | null,
  "contract_end_date":  "YYYY-MM-DD" | null,
  "confidence": 0.0..1.0,
  "evidence_quote": "the exact transcript line that mentions the date, or null"
}

Rules:
- "expected_live_date" = when the new contract STARTS supplying the customer.
- "contract_end_date" = when the contract expires (rare; usually 24-48 months later).
- If the transcript only says relative phrases like "next month" without a clear
  reference date, return null.
- If two competing dates appear, choose the one the customer AGREES TO.
- If unclear, prefer null over guessing.
- Today's date is the call's `created_at` — use it for relative resolution.
- "confidence" — your subjective certainty 0..1.
- Output JSON ONLY. No prose."""


def _regex_candidates(transcript: str) -> list[str]:
    hits: list[str] = []
    for pat in _DATE_PATTERNS:
        for m in re.finditer(pat, transcript, flags=re.IGNORECASE):
            hits.append(m.group(0))
            if len(hits) >= 12:
                return hits
    return hits


@LLM_RETRY
async def _llm_extract(transcript: str, call_date_iso: str) -> dict:
    user = (
        f"CALL DATE (today's reference): {call_date_iso}\n\n"
        f"TRANSCRIPT:\n{transcript}\n\n"
        "Return the JSON now."
    )
    # Uses the globally-configured model (currently Opus 4.7 via
    # OpenRouter). Date extraction is structured + cheap context, so a
    # smaller model would suffice — TODO: add model_override path on
    # _call_llm and switch to Haiku 4.5 for ~10x cost reduction.
    raw = await _call_llm(
        user,
        system=SYSTEM_PROMPT,
        timeout=45.0,
    )
    return json.loads(raw)


def _parse_iso(s: str | None) -> Optional[_date]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).date()
    except (ValueError, TypeError):
        return None


async def extract_dates_for_call(call: Call) -> dict:
    """Pure-extraction front door — returns the dict above without DB writes.

    Caller is responsible for applying the result. Empty dict if there's
    no transcript or zero regex candidates.
    """
    if not call.transcript:
        return {}
    candidates = _regex_candidates(call.transcript)
    if not candidates:
        log.info(f"📅 DATE_EXTRACTOR call_id={call.id} no candidates, skip LLM")
        return {
            "expected_live_date": None,
            "contract_end_date": None,
            "confidence": 1.0,
            "evidence_quote": None,
            "skipped": True,
        }

    call_date = call.created_at.date().isoformat() if call.created_at else _date.today().isoformat()
    try:
        verdict = await _llm_extract(call.transcript, call_date)
    except Exception as e:  # noqa: BLE001
        log.warning(f"📅 DATE_EXTRACTOR call_id={call.id} LLM error: {e}")
        return {}

    log.info(
        f"📅 DATE_EXTRACTOR call_id={call.id} "
        f"live={verdict.get('expected_live_date')} "
        f"end={verdict.get('contract_end_date')} "
        f"conf={verdict.get('confidence')}"
    )
    return verdict


async def DateExtractorAgent(call_id: str, db: Session) -> dict:
    """End-to-end: extract + write to CustomerDeal. Idempotent."""
    call = db.query(Call).filter_by(id=call_id).first()
    if not call:
        return {"error": "call_not_found"}
    verdict = await extract_dates_for_call(call)
    if not verdict or verdict.get("skipped"):
        return verdict or {}

    if call.deal_id:
        deal = db.query(CustomerDeal).filter_by(id=call.deal_id).first()
        if deal:
            live = _parse_iso(verdict.get("expected_live_date"))
            if live and deal.expected_live_date != live:
                deal.expected_live_date = live
                db.commit()
                log.info(
                    f"📅 DATE_EXTRACTOR applied deal_id={deal.id} "
                    f"expected_live_date={live}"
                )
    return verdict
