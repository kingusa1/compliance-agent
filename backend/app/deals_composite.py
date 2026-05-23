"""Composite Deal verdict — weighted average across the calls in a Deal.

Watt thinks per-Deal, not per-Call: a single bad verbal kills the whole
contract even if the intro + qualification scored 100%. Mirrors XLSX
deep-dive §1.6 + §2.12 (rejection rows persist across months tied to a
customer/site, not individual calls). Threshold ≥80% per Claude-Design
Customer-Lifecycle screen 4.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import Call, CustomerDeal


# Per-call-type weight (Watt's mental model — verbal contract dominates).
CALL_TYPE_WEIGHT: dict[str, int] = {
    "lead_gen": 20,
    "qualification": 30,
    "closer": 50,
    "verbal": 50,           # alias for legacy data
    "amendment": 30,
    "passover": 10,
    "standalone_loa": 30,
    "full": 50,
    "c_call": 30,
}
DEFAULT_WEIGHT = 20
COMPOSITE_THRESHOLD_PCT = 80.0


def _parse_score(s: str | int | float | None) -> float | None:
    """Accept 75, 75.0, "75", "75/100", or None.

    Returns the score as a 0-100 percentage, or None if unparseable.
    """
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    text = str(s).strip()
    if "/" in text:
        num, denom = text.split("/", 1)
        try:
            n = float(num.strip())
            d = float(denom.strip())
            if d <= 0:
                return None
            return (n / d) * 100.0
        except ValueError:
            return None
    try:
        return float(text)
    except ValueError:
        return None


def composite_from_calls(deal_id: Any, calls: list[Call]) -> dict[str, Any]:
    """Pure composite calc — takes pre-fetched calls so a bulk caller (e.g.
    ``list_deals``) can compute scores for many deals without an extra
    per-deal DB hop. Same return shape as ``compute_composite_verdict``.
    """
    per_call: list[dict[str, Any]] = []
    weighted_sum = 0.0
    weight_total = 0.0
    worst = "PASS"
    scored = 0
    for c in calls:
        pct = _parse_score(c.score)
        ct = c.call_type or "full"
        weight = CALL_TYPE_WEIGHT.get(ct, DEFAULT_WEIGHT)
        if pct is None:
            per_call.append({
                "id": c.id, "call_type": ct, "score": None, "weight": weight,
                "status": c.status, "agent": c.agent_name,
            })
            continue
        scored += 1
        weighted_sum += pct * weight
        weight_total += weight
        if pct < 70:
            worst = "FAIL"
        elif pct < 80 and worst != "FAIL":
            worst = "REVIEW"
        per_call.append({
            "id": c.id, "call_type": ct, "score": pct, "weight": weight,
            "status": c.status, "agent": c.agent_name,
        })
    composite_pct = (weighted_sum / weight_total) if weight_total > 0 else None
    return {
        "deal_id": str(deal_id),
        "composite_pct": round(composite_pct, 1) if composite_pct is not None else None,
        "threshold_pct": COMPOSITE_THRESHOLD_PCT,
        "threshold_met": composite_pct is not None and composite_pct >= COMPOSITE_THRESHOLD_PCT,
        "worst_action": worst if scored > 0 else "PENDING",
        "calls_scored": scored,
        "calls_total": len(calls),
        "per_call": per_call,
    }


def compute_composite_verdict(deal_id: Any, db: Session) -> dict[str, Any]:
    """Composite verdict for a Deal — weighted-avg of its calls' scores."""
    deal = db.query(CustomerDeal).filter_by(id=deal_id).first()
    if deal is None:
        return {
            "deal_id": str(deal_id),
            "composite_pct": None,
            "calls_scored": 0,
            "calls_total": 0,
            "threshold_met": False,
            "worst_action": "PENDING",
            "per_call": [],
        }
    calls = db.query(Call).filter_by(deal_id=deal_id).all()
    return composite_from_calls(deal_id, calls)


