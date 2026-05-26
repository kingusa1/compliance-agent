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
from typing import Literal

from sqlalchemy.orm import Session

from app._clock import utcnow
from app.models import Call, CallSegment, ComplianceDecision


CONFIDENCE_FLOOR = 0.55

# Closed set of provenance tags written to ``Call.compliance_source`` /
# ``ComplianceDecision.actor_type`` so downstream queries can filter by
# how the verdict was reached.
ComplianceSource = Literal["bucket_aggregator", "auto"]

# Worst-bucket-wins precedence across per-segment ``CallSegment.bucket``
# values. Mirror of ``pipeline._BUCKET_RANK`` — kept local so importing
# pipeline.py from compliance.py doesn't create a circular dependency.
# A new bucket value added in pipeline.py MUST be mirrored here.
_BUCKET_RANK = {"pass": 0, "coaching": 1, "review": 2, "blocked": 3}

# Bucket → call-level compliance_status. Mirror of the mapping inside
# ``pipeline._step_score``.
_BUCKET_TO_STATUS = {
    "pass": "compliant",
    "coaching": "compliant",
    "review": "pending",
    "blocked": "non_compliant",
}


def _aggregate_segments_status(buckets: list[str | None]) -> str:
    """Compute the call-level ``compliance_status`` from a list of segment
    buckets via worst-bucket-wins. Returns ``"pending"`` if the list is
    empty or contains only unknown bucket values."""
    worst_rank = -1
    worst = "pass"
    for b in buckets:
        if not b:
            continue
        rank = _BUCKET_RANK.get(b)
        if rank is None:
            continue
        if rank > worst_rank:
            worst_rank = rank
            worst = b
    if worst_rank < 0:
        return "pending"
    return _BUCKET_TO_STATUS.get(worst, "pending")

log = logging.getLogger("compliance")


def _effective_verdict(cp: dict) -> str:
    """Return the authoritative verdict for a checkpoint dict, honoring reviewer overrides."""
    return cp.get("reviewer_verdict") or cp.get("verdict") or cp.get("status") or "flagged"


def _confidence(cp: dict) -> float:
    """Coerce a checkpoint's ``confidence`` value to a 0-1 float for the
    ``CONFIDENCE_FLOOR`` (0.55) gate in the V1 rule path.

    Numeric values pass through. Label strings are mapped: ``high`` → 0.95,
    ``medium`` → 0.75, ``low`` → 0.40 (below floor, triggers ``pending``).
    Any unknown/missing value falls back to ``1.0`` — i.e. treats unknown
    confidence as fully trusted to avoid spuriously routing pre-2026-05-12
    checkpoints (which never carried a confidence field) into the
    needs-review branch on backfill.
    """
    c = cp.get("confidence")
    if isinstance(c, (int, float)):
        return float(c)
    if isinstance(c, str):
        return {"high": 0.95, "medium": 0.75, "low": 0.4}.get(c.lower(), 1.0)
    return 1.0


def _write_decision_row(
    call: Call,
    db: Session,
    status: str,
    failing: list[str] | None,
    source: ComplianceSource,
) -> None:
    """Stamp the ComplianceDecision audit row for this status transition.

    Demotes any prior ``is_current=True`` decision so exactly one row carries
    the live verdict. Uses ``SELECT ... FOR UPDATE`` to serialise concurrent
    pipelines finalising the same ``call_id`` — without the row lock, two
    workers can both read the same prior row, both demote it, both INSERT
    a new ``is_current=True`` row, and leave two rows with the live flag
    set. SQLite (CI) ignores ``FOR UPDATE`` silently; Postgres (prod)
    honours it. Caller is responsible for ``db.commit()``.
    """
    call.compliance_source = source
    call.compliance_decided_at = utcnow()
    call.compliance_decided_by = "system"

    prior = (
        db.query(ComplianceDecision)
        .filter_by(call_id=call.id, is_current=True)
        .with_for_update()
        .first()
    )
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


def derive_compliance(call: Call, db: Session, *, commit: bool = True) -> str:
    """Set ``call.compliance_*`` fields based on the call's evidence.

    Routes to the segments path when ``CallSegment`` rows exist for this call
    (the modern pipeline); falls back to the V1 flat ``checkpoint_results``
    rules otherwise. Returns the final ``compliance_status`` string.

    ``commit=False`` lets a batch caller (e.g. the rederive backfill
    endpoint) accumulate multiple calls' changes into a single outer
    transaction so the whole pass is atomic. Default ``True`` preserves
    the single-call contract used by ``pipeline._step_finalize``.
    """
    # ── Segments path (modern pipeline) ─────────────────────────────────
    # Aggregate the call-level status from per-segment buckets using
    # worst-bucket-wins. This RECOMPUTES rather than trusts
    # ``call.compliance_status`` because pre-2026-05-26 the legacy V1
    # rules would overwrite the bucket-aggregator's correct status with
    # the wrong value — so existing rows in prod carry stale fields the
    # backfill endpoint exists to repair. The V1 flat-list rules below
    # are intentionally bypassed: they ignore severity tiers and would
    # either demote coaching → non_compliant (any medium partial trips
    # Rule 2) or revert blocked → pending (any needs_review checkpoint
    # trips Rule 1).
    seg_rows = (
        db.query(CallSegment.bucket).filter_by(call_id=call.id).all()
    )
    seg_count = len(seg_rows)
    if seg_count > 0:
        derived = _aggregate_segments_status([row[0] for row in seg_rows])
        # Write back so the ORM object and the DB row converge.
        call.compliance_status = derived
        # Keep call.compliant in lockstep with the strict "all clean"
        # contract (only worst_bucket == "pass" qualifies). Coaching
        # calls still show on the Awaiting-review tab because compliant
        # stays False even though compliance_status is "compliant".
        all_pass = all((row[0] or "") == "pass" for row in seg_rows)
        call.compliant = all_pass
        failing: list[str] | None = None
        if derived == "non_compliant":
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
        if derived != "pending":
            _write_decision_row(call, db, derived, failing, source="bucket_aggregator")
        if commit:
            db.commit()
        log.info(
            "derive_compliance: segments_path call_id=%s segments=%d status=%s",
            call.id, seg_count, derived,
        )
        return derived

    # ── V1 fallback path ─────────────────────────────────────────────────
    try:
        cps = json.loads(call.checkpoint_results or "[]")
    except (TypeError, ValueError):
        cps = []

    if not cps:
        call.compliance_status = "pending"
        if commit:
            db.commit()
        return "pending"

    # Rule 1: anything flagged for review keeps the call pending
    for cp in cps:
        if cp.get("needs_review") or _confidence(cp) < CONFIDENCE_FLOOR:
            call.compliance_status = "pending"
            if commit:
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
    if commit:
        db.commit()
    return status
