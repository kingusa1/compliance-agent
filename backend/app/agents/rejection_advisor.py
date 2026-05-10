"""RejectionAdvisorAgent — fill Category + Fix Required + Severity.

For every non-compliant call (or every Rejection row), Opus 4.7 reads
the failed checkpoints + transcript snippet and emits the four
tracker columns that are otherwise empty in production today:

  - category    (one of the 4 master buckets from rejection_lists.xlsx)
  - fix_required (1-2 sentence operations-team-tone instruction)
  - severity    (CRITICAL / HIGH / MEDIUM / LOW — drives DeadlineComputer)
  - confidence  (0..1, agent's certainty)

Used by:
- pipeline.py post-finalize: each newly-created Rejection.
- /api/admin/backfill-rejection-advisor: walks legacy rejections with
  NULL category or fix_required and fills them in.

Idempotent: running on a rejection that already has both fields is a
no-op unless `force=True`.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.analysis import _call_llm
from app.logger import log
from app.models import Call, Rejection
from app.resilience import LLM_RETRY


# Categories from `compliance-docs/COMPLIANCE XAI/Compliance Xai
# rejection lists.xlsx` (markdown extract at
# .planning/phase2-docs/compliance_xai_rejection_lists.md). The 4
# master buckets observed in real Watt rejection narratives.
ALLOWED_CATEGORIES = {
    "ADMIN ERROR",
    "PROCESS FAILURE",
    "COMPLIANCE BREACH",
    "RE-WORK NEEDED",
}


SYSTEM_PROMPT = """You are the Rejection Advisor Agent for Watt Utilities — a UK third-party-intermediary energy broker.

When a sales call fails compliance, you decide:

1. CATEGORY — one of EXACTLY these four (use the literal string):
   • "ADMIN ERROR"        — wrong name on contract, missing LOA, wrong DD, etc.
   • "PROCESS FAILURE"    — bacs denied, contract not sent, missing docs, etc.
   • "COMPLIANCE BREACH"  — TPI mis-disclosure, mis-selling, vulnerability not handled
   • "RE-WORK NEEDED"     — supplier sent it back asking for a specific fix

2. FIX_REQUIRED — a 1-2 sentence operations-team-tone instruction
   ("send new LOA with correct trading name", "redo bacs with confirmed
   sort code", "amendment + confirmation call to clarify broker status").
   Match the voice in this real Watt example: "do contract with kevin",
   "Confirm correct company details and redo if needs be".
   Keep it CONCISE and ACTION-FIRST. Lowercase ops-style is fine.

3. SEVERITY — one of:
   • CRITICAL — compliance breach with regulatory exposure (Ofgem-relevant)
   • HIGH     — process failure blocking go-live
   • MEDIUM   — admin rework, contract not yet at risk
   • LOW      — cosmetic / non-blocking

4. EVIDENCE_QUOTE — exact line(s) from the transcript proving the failure.

Return ONLY valid JSON:

{
  "category":      "ADMIN ERROR" | "PROCESS FAILURE" | "COMPLIANCE BREACH" | "RE-WORK NEEDED",
  "fix_required":  "<1-2 sentence ops-tone instruction>",
  "severity":      "CRITICAL" | "HIGH" | "MEDIUM" | "LOW",
  "confidence":    0.0..1.0,
  "evidence_quote": "<exact transcript quote>"
}

Output JSON ONLY. No prose."""


@LLM_RETRY
async def _llm_advise(transcript: str, failed_summary: str, supplier: str | None) -> dict:
    user = (
        f"SUPPLIER: {supplier or 'Unknown'}\n\n"
        f"FAILED CHECKPOINTS (from the v2 analyzer):\n{failed_summary}\n\n"
        f"TRANSCRIPT:\n{transcript[:6000]}\n\n"
        "Return the JSON now."
    )
    raw = await _call_llm(user, system=SYSTEM_PROMPT, timeout=60.0)
    return json.loads(raw)


def _summarize_failures(call: Call) -> str:
    """Build the failed-checkpoints summary block fed to the LLM."""
    if not call.checkpoint_results:
        return f"Rule: {call.rule_id or 'UNKNOWN'} (score {call.score})"
    try:
        cps = json.loads(call.checkpoint_results)
    except Exception:
        return f"Rule: {call.rule_id or 'UNKNOWN'} (score {call.score})"
    failures = [c for c in cps if c.get("status") == "fail"]
    if not failures:
        return f"Rule: {call.rule_id or 'UNKNOWN'} (no individual failures parsed)"
    out = []
    for f in failures:
        line = f"- {f.get('name', '?')}"
        if f.get("notes"):
            line += f" — {f['notes']}"
        out.append(line)
    return "\n".join(out)


async def advise_rejection(call: Call) -> dict:
    """Pure-LLM front door — returns the verdict dict, no DB write."""
    if not call.transcript:
        return {}
    summary = _summarize_failures(call)
    try:
        verdict = await _llm_advise(
            call.transcript, summary, call.detected_supplier
        )
    except Exception as e:  # noqa: BLE001
        log.warning(f"📋 REJECTION_ADVISOR call_id={call.id} LLM error: {e}")
        return {}

    cat = (verdict.get("category") or "").upper()
    if cat not in ALLOWED_CATEGORIES:
        log.warning(
            f"📋 REJECTION_ADVISOR call_id={call.id} category={cat!r} "
            f"not in vocab — coercing to ADMIN ERROR"
        )
        verdict["category"] = "ADMIN ERROR"
    else:
        verdict["category"] = cat

    sev = (verdict.get("severity") or "").upper()
    if sev not in {"CRITICAL", "HIGH", "MEDIUM", "LOW"}:
        verdict["severity"] = "MEDIUM"
    else:
        verdict["severity"] = sev

    log.info(
        f"📋 REJECTION_ADVISOR call_id={call.id} "
        f"category={verdict['category']} severity={verdict['severity']} "
        f"confidence={verdict.get('confidence')}"
    )
    return verdict


async def RejectionAdvisorAgent(
    *,
    rejection_id: str | None = None,
    call_id: str | None = None,
    db: Session,
    force: bool = False,
) -> dict:
    """End-to-end runner — extract + write to Rejection row.

    Pass either ``rejection_id`` (apply to one Rejection) or ``call_id``
    (apply to all Rejection rows of that Call). When ``force=False``
    (default) skips rejections that already have both ``category`` and
    ``fix_required`` set.
    """
    rejections: list[Rejection] = []
    if rejection_id:
        r = db.query(Rejection).filter_by(id=rejection_id).first()
        if r:
            rejections = [r]
    elif call_id:
        rejections = db.query(Rejection).filter_by(call_id=call_id).all()
    else:
        return {"error": "must pass rejection_id or call_id"}

    if not rejections:
        return {"updated": 0, "skipped": 0, "missing_call": 0}

    updated, skipped, missing_call = 0, 0, 0
    for rej in rejections:
        if not force and rej.category and rej.fix_required:
            skipped += 1
            continue
        call = db.query(Call).filter_by(id=rej.call_id).first() if rej.call_id else None
        if not call:
            missing_call += 1
            continue
        verdict = await advise_rejection(call)
        if not verdict:
            continue
        rej.category = verdict["category"]
        rej.fix_required = verdict["fix_required"]
        # severity isn't on Rejection schema today — the deadline computer
        # consumes it via the verdict dict, not from DB. We store it on
        # field_sources for audit.
        try:
            sources = dict(rej.field_sources or {})
            sources["severity"] = f"ai:rejection_advisor:{verdict['severity']}"
            sources["category"] = "ai:rejection_advisor"
            sources["fix_required"] = "ai:rejection_advisor"
            rej.field_sources = sources
        except Exception:
            pass
        updated += 1

    db.commit()
    log.info(
        f"📋 REJECTION_ADVISOR batch updated={updated} skipped={skipped} "
        f"missing_call={missing_call}"
    )
    return {"updated": updated, "skipped": skipped, "missing_call": missing_call}
