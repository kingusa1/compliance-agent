"""Pure-function compliance derivation from checkpoint state.

Two compliance pathways exist in this codebase:

1. **Modern segments path** (2026-05-12 taxonomy rebuild and after).
   ``app.pipeline._step_score`` aggregates per-segment ``CallSegment.bucket``
   into a worst-bucket-wins call-level ``compliance_status`` (pass / coaching
   → compliant; review → pending; blocked → non_compliant). The
   ``call.checkpoint_results`` flat column is still written (union of all
   segments + ``not_scored`` placeholders) so the UI's per-rule grid renders
   every checkpoint, but the AUTHORITATIVE status comes from the bucket
   aggregator. This function MUST preserve the bucket-based status when
   ``CallSegment`` rows exist — overwriting it with the V1 flat-list rules
   below produces inverted verdicts (2026-05-26 incident: 24e184ee coaching
   demoted to non_compliant; 4c62d964 blocked demoted to pending).

2. **V1 fallback path** (legacy / single-rubric scoring with no segments).
   No ``CallSegment`` rows are written. Status is derived purely from the
   flat ``checkpoint_results`` JSON using these rules, in order:
   - Empty checkpoint_results → pending.
   - Any checkpoint with ``needs_review=True`` OR confidence below
     ``CONFIDENCE_FLOOR`` → pending (no ``ComplianceDecision`` row written —
     human must decide).
   - Any checkpoint whose effective verdict is ``fail`` / ``partial`` /
     ``unverified`` → non_compliant.
   - All effective verdicts are ``pass`` → compliant.

Effective verdict = ``reviewer_verdict`` (if set) else ``verdict`` else
``status``. Reviewer overrides take precedence on either pathway via the
HITL endpoints in ``hitl_routes.py``.
"""
from __future__ import annotations

import json
import logging
import uuid

from sqlalchemy.orm import Session

from app._clock import utcnow
from app.models import Call, CallSegment, ComplianceDecision


CONFIDENCE_FLOOR = 0.55

log = logging.getLogger("compliance")


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


def _write_decision_row(
    call: Call,
    db: Session,
    status: str,
    failing: list[str] | None,
    source: str,
) -> None:
    """Stamp the ComplianceDecision audit row for this status transition.

    Demotes any prior ``is_current=True`` decision so exactly one row carries
    the live verdict. Caller is responsible for ``db.commit()``.
    """
    call.compliance_source = source
    call.compliance_decided_at = utcnow()
    call.compliance_decided_by = "system"

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


def derive_compliance(call: Call, db: Session) -> str:
    """Set ``call.compliance_*`` fields based on the call's evidence.

    Routes to the segments path when ``CallSegment`` rows exist for this call
    (the modern pipeline); falls back to the V1 flat ``checkpoint_results``
    rules otherwise. Returns the final ``compliance_status`` string.
    """
    # ── Segments path (modern pipeline) ─────────────────────────────────
    # If _step_score already aggregated per-segment buckets into a
    # call-level status, that decision is authoritative. We MUST NOT
    # re-run the V1 flat-list rules — they ignore severity tiers and
    # would either demote coaching → non_compliant (any medium partial
    # triggers Rule 2) or revert blocked → pending (any needs_review
    # checkpoint triggers Rule 1). ``Call.compliance_status`` is NOT NULL
    # with server_default ``"pending"``, so any segment-bearing call has
    # a valid status here.
    seg_count = db.query(CallSegment).filter_by(call_id=call.id).count()
    if seg_count > 0:
        current = (call.compliance_status or "pending").strip() or "pending"
        failing: list[str] | None = None
        if current == "non_compliant":
            # Surface the failing rule names for the audit row so the
            # decision log stays useful even on the segments path.
            try:
                cps = json.loads(call.checkpoint_results or "[]") or []
            except (TypeError, ValueError):
                cps = []
            failing = [
                cp.get("id") or cp.get("name") or "<unknown>"
                for cp in cps
                if _effective_verdict(cp) != "pass"
            ] or None
        # No audit row for "pending" — matches V1 semantics (no row
        # written when human input is still required).
        if current != "pending":
            _write_decision_row(call, db, current, failing, source="bucket_aggregator")
        db.commit()
        log.info(
            f"derive_compliance: segments_path call_id={call.id} "
            f"segments={seg_count} status={current}"
        )
        return current

    # ── V1 fallback path ─────────────────────────────────────────────────
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
    _write_decision_row(call, db, status, failing or None, source="auto")
    db.commit()
    return status
