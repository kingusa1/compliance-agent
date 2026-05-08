"""Pure-function compliance derivation from checkpoint state.

Decision rules, evaluated in order:
- Empty checkpoint_results → pending.
- Any checkpoint with `needs_review=True` OR confidence below CONFIDENCE_FLOOR → pending
  (no ComplianceDecision row written — human must decide).
- Any checkpoint whose effective verdict is "fail" or "partial" or "unverified" → non_compliant.
- All effective verdicts are "pass" → compliant.

Effective verdict = reviewer_verdict (if set) else verdict (if set) else status.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import Call, ComplianceDecision


CONFIDENCE_FLOOR = 0.55


def _effective_verdict(cp: dict) -> str:
    """Return the authoritative verdict for a checkpoint dict, honoring reviewer overrides."""
    return cp.get("reviewer_verdict") or cp.get("verdict") or cp.get("status") or "flagged"


def _confidence(cp: dict) -> float:
    c = cp.get("confidence")
    if isinstance(c, (int, float)):
        return float(c)
    # Some pipelines store confidence as a label ("high", "medium", "low"). Map:
    if isinstance(c, str):
        return {"high": 0.95, "medium": 0.75, "low": 0.4}.get(c.lower(), 1.0)
    return 1.0


def derive_compliance(call: Call, db: Session) -> str:
    """Inspect call.checkpoint_results and set call.compliance_* fields. Returns the new status."""
    try:
        cps = json.loads(call.checkpoint_results or "[]")
    except (TypeError, ValueError):
        cps = []

    if not cps:
        call.compliance_status = "pending"
        db.commit()
        return "pending"

    # Rule 1: anything flagged for review keeps the call pending
    for cp in cps:
        if cp.get("needs_review") or _confidence(cp) < CONFIDENCE_FLOOR:
            call.compliance_status = "pending"
            db.commit()
            return "pending"

    # Rule 2: any non-pass effective verdict → non_compliant
    failing = []
    for cp in cps:
        v = _effective_verdict(cp)
        if v != "pass":
            failing.append(cp.get("id") or cp.get("name") or "<unknown>")

    status = "non_compliant" if failing else "compliant"

    call.compliance_status = status
    call.compliance_source = "auto"
    call.compliance_decided_at = datetime.utcnow()
    call.compliance_decided_by = "system"

    # Demote any prior is_current decision (unlikely on first run, but safe)
    prior = db.query(ComplianceDecision).filter_by(call_id=call.id, is_current=True).first()
    if prior:
        prior.is_current = False

    db.add(ComplianceDecision(
        id=str(uuid.uuid4()),
        call_id=call.id,
        status=status,
        actor_type="system",
        actor_id="system",
        failing_checkpoints=json.dumps(failing) if failing else None,
        is_current=True,
    ))
    db.commit()
    return status
