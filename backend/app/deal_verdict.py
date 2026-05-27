"""Deal-level verdict aggregator (Pillar 3 / L3).

Rolls up per-call scores + worst_action into a single deal-level
verdict. Surfaced via GET /api/deals/{id}/verdict and consumed by the
/deals/[id] page.

Composite score
---------------
Weighted average of each call's pass-rate (parsed from the "X/Y"
``Call.score`` string), weighted per the design_decisions matrix:

    closer:         50%
    lead_gen:       30%
    standalone_loa: 20%
    amendment:      20%   (corrective; folded into the average when
                          present)

Calls without a ``score`` (still in flight, or v1-mode) contribute
nothing — the weighted average normalises across calls that *did*
score so a single Closer-only deal still produces a sensible number.

Worst-action escalation
-----------------------
Worst across all calls in the 5-state ladder used by L4
ComplianceDecisionPanel: PASS < REVIEW < COACHING < FAIL < BLOCK.

Missing calls
-------------
``required_phases(supplier)`` minus the set of completed phases. C-call
and amendment never appear here — they're corrective, not required.
"""

from __future__ import annotations

from typing import Iterable, Optional

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deal_lifecycle import (
    call_type_to_phase,
    derive_lifecycle_status,
    required_phases,
)
from app.models import Call, CustomerDeal
from app.segment_chips import fetch_segments_by_call_ids


# ---------------------------------------------------------------------------
# Weights & action ladder
# ---------------------------------------------------------------------------

WEIGHTS: dict[str, float] = {
    "lead_gen": 0.30,
    "closer": 0.50,
    "standalone_loa": 0.20,
    "amendment": 0.20,
}

# Lower index = milder. worst_action picks the highest index seen.
_ACTION_LADDER: list[str] = ["PASS", "REVIEW", "COACHING", "FAIL", "BLOCK"]
_ACTION_RANK: dict[str, int] = {a: i for i, a in enumerate(_ACTION_LADDER)}


# ---------------------------------------------------------------------------
# Pydantic shape returned by /api/deals/{id}/verdict
# ---------------------------------------------------------------------------


class CallBreakdown(BaseModel):
    call_id: str
    call_type: Optional[str] = None
    phase: Optional[str] = None
    score_fraction: Optional[float] = None  # 0.0..1.0; None if no score parsed
    score_raw: Optional[str] = None  # e.g. "5/7"
    action: Optional[str] = None  # PASS|REVIEW|COACHING|FAIL|BLOCK
    completed_at: Optional[str] = None


class DealVerdict(BaseModel):
    composite_score: Optional[float] = None  # 0..100, or None if no calls scored
    worst_action: str = "PASS"
    missing_calls: list[str]
    call_breakdown: list[CallBreakdown]
    lifecycle_status: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_score(raw: str | None) -> Optional[float]:
    """Parse a "X/Y" score string into a 0..1 fraction. Returns None
    on any malformed input — callers normalise by ignoring None."""
    if not raw:
        return None
    if "/" not in raw:
        return None
    try:
        num_s, den_s = raw.split("/", 1)
        num = float(num_s.strip())
        den = float(den_s.strip())
        if den <= 0:
            return None
        return max(0.0, min(1.0, num / den))
    except Exception:
        return None


def _call_action(call: Call) -> Optional[str]:
    """Best-available action label for a single call. Reads
    Call.compliance_status first (set by reviewer / derive_compliance),
    falls back to PASS/REVIEW heuristic from compliant + score."""
    cs = (getattr(call, "compliance_status", None) or "").lower()
    if cs in ("compliant",):
        return "PASS"
    if cs in ("non_compliant", "failed"):
        return "FAIL"
    if cs in ("needs_review", "needs_manual_review", "pending"):
        if call.compliant is False:
            return "REVIEW"
        return "REVIEW"
    if call.compliant is True:
        return "PASS"
    if call.compliant is False:
        return "REVIEW"
    return None


def _worst_action(actions: Iterable[Optional[str]]) -> str:
    seen = [a for a in actions if a in _ACTION_RANK]
    if not seen:
        return "PASS"
    return max(seen, key=lambda a: _ACTION_RANK[a])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def aggregate_deal_verdict(deal_id, db: Session) -> DealVerdict:
    """Compute the rolled-up verdict for ``deal_id``. Raises
    ``ValueError`` if the deal does not exist — the route layer maps
    that to HTTP 404."""
    deal = db.query(CustomerDeal).filter(CustomerDeal.id == deal_id).first()
    if not deal:
        raise ValueError(f"deal {deal_id} not found")

    calls = (
        db.query(Call)
        .filter(Call.deal_id == deal.id)
        .order_by(Call.created_at.asc())
        .all()
    )

    # Wave-26 (2026-05-27) — a single audio file can contain multiple
    # segments (lead_gen + pre_sales + verbal + loa). Without this the
    # deal-detail page said "2 of 4 required calls missing" even when
    # both uploaded files covered Pre-Sales + Verbal between them.
    # Bulk-fetch every detected segment kind so completed_phases is the
    # UNION of (call_type) and (every segment kind in every call).
    call_ids = [str(c.id) for c in calls if c.completed_at]
    segs_by_call = fetch_segments_by_call_ids(db, call_ids) if call_ids else {}

    breakdown: list[CallBreakdown] = []
    weighted_sum = 0.0
    weight_total = 0.0
    actions: list[Optional[str]] = []
    completed_phases: set[str] = set()

    for c in calls:
        phase = call_type_to_phase(c.call_type)
        frac = _parse_score(c.score)
        action = _call_action(c)
        actions.append(action)

        if c.completed_at:
            if phase:
                completed_phases.add(phase)
            # Wave-26 — also count every detected segment phase. Segment
            # `kind` is already in the canonical taxonomy (lead_gen,
            # pre_sales, verbal, loa) so no remapping needed.
            for chip in segs_by_call.get(str(c.id), []):
                k = (chip.kind or "").lower()
                if k:
                    completed_phases.add(k)

        if phase and frac is not None:
            w = WEIGHTS.get(phase)
            if w:
                weighted_sum += frac * w
                weight_total += w

        breakdown.append(
            CallBreakdown(
                call_id=str(c.id),
                call_type=c.call_type,
                phase=phase,
                score_fraction=frac,
                score_raw=c.score,
                action=action,
                completed_at=c.completed_at.isoformat() if c.completed_at else None,
            )
        )

    composite: Optional[float] = None
    if weight_total > 0:
        composite = round((weighted_sum / weight_total) * 100.0, 2)

    required = set(required_phases(deal.supplier))
    missing = sorted(required - completed_phases)

    lifecycle = derive_lifecycle_status(deal, calls)

    return DealVerdict(
        composite_score=composite,
        worst_action=_worst_action(actions),
        missing_calls=missing,
        call_breakdown=breakdown,
        lifecycle_status=lifecycle,
    )
