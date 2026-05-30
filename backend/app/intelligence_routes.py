"""
Intelligence panel endpoints (Plan §5f).

Four read-only aggregations feed the new Dashboard Intelligence panel:

  GET /api/intelligence/by-supplier      → compliance % per supplier
  GET /api/intelligence/by-agent         → top-N agents by compliance %
  GET /api/intelligence/by-call-type     → call_type donut
  GET /api/intelligence/trend            → 30-day compliance trend

Every endpoint operates on already-completed calls only (status='completed').
Pending / failed / needs_manual_review rows are excluded so the dashboard
shows real signal, not pipeline noise.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from app._clock import utcnow
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.auth import current_user
from app.database import get_db
from app.models import Call


_COMPLIANT_INT = case((Call.compliant.is_(True), 1), else_=0)


# Auth gate (2026-05-30 security audit): all intelligence aggregations expose
# compliance analytics (per-supplier / per-agent rates) — require an authenticated
# user. Frontend apiFetch already attaches the Supabase bearer to every call.
intelligence_router = APIRouter(
    prefix="/api/intelligence",
    tags=["intelligence"],
    dependencies=[Depends(current_user)],
)


def _completed(db: Session):
    return db.query(Call).filter(Call.status == "completed")


def _pct(num: int, denom: int) -> float:
    if not denom:
        return 0.0
    return round((num / denom) * 100.0, 1)


@intelligence_router.get("/by-supplier")
def by_supplier(db: Session = Depends(get_db)) -> dict:
    """Compliance % grouped by detected_supplier, descending by call volume.

    Suppliers with zero completed calls aren't included. Unknown supplier
    rows are bucketed under ``"Unknown"``.
    """
    rows = (
        _completed(db)
        .with_entities(
            func.coalesce(Call.detected_supplier, "Unknown").label("supplier"),
            func.count(Call.id).label("total"),
            func.sum(_COMPLIANT_INT).label("compliant"),
        )
        .group_by("supplier")
        .all()
    )

    items = []
    for r in rows:
        total = int(r.total or 0)
        compliant = int(r.compliant or 0)
        items.append(
            {
                "supplier": r.supplier or "Unknown",
                "total": total,
                "compliant": compliant,
                "compliance_pct": _pct(compliant, total),
            }
        )
    items.sort(key=lambda x: (-x["total"], x["supplier"]))
    return {"items": items}


@intelligence_router.get("/by-agent")
def by_agent(
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
) -> dict:
    """Top-N agents by compliance %, with absolute call counts as a tiebreak.

    Agents with fewer than 3 completed calls are excluded so a single
    compliant call doesn't inflate the leaderboard.
    """
    rows = (
        _completed(db)
        .filter(Call.agent_name.isnot(None), Call.agent_name != "Unknown")
        .with_entities(
            Call.agent_name.label("agent"),
            func.count(Call.id).label("total"),
            func.sum(_COMPLIANT_INT).label("compliant"),
        )
        .group_by(Call.agent_name)
        .having(func.count(Call.id) >= 3)
        .all()
    )

    items = []
    for r in rows:
        total = int(r.total or 0)
        compliant = int(r.compliant or 0)
        items.append(
            {
                "agent": r.agent,
                "total": total,
                "compliant": compliant,
                "compliance_pct": _pct(compliant, total),
            }
        )
    items.sort(key=lambda x: (-x["compliance_pct"], -x["total"], x["agent"]))
    return {"items": items[:limit]}


@intelligence_router.get("/by-call-type")
def by_call_type(db: Session = Depends(get_db)) -> dict:
    """Donut data — count of completed calls per canonical call_type.

    The 2026-05-12 rebuild locked call_type to one of
    {lead_gen, pre_sales, verbal, loa}; older NULL rows surface as
    ``Unclassified``.
    """
    rows = (
        _completed(db)
        .with_entities(
            func.coalesce(Call.call_type, "Unclassified").label("call_type"),
            func.count(Call.id).label("total"),
        )
        .group_by("call_type")
        .all()
    )

    order = ["lead_gen", "pre_sales", "verbal", "loa", "Unclassified"]
    by_key = {r.call_type: int(r.total or 0) for r in rows}
    items = [
        {"call_type": k, "total": by_key.get(k, 0)}
        for k in order
        if by_key.get(k, 0) > 0
    ]
    # Catch any unknown call_types that slipped through (defensive).
    for k, v in by_key.items():
        if k not in order:
            items.append({"call_type": k, "total": v})
    return {"items": items}


@intelligence_router.get("/trend")
def trend(
    days: int = Query(30, ge=7, le=180),
    bucket: Literal["day", "week"] = Query("week"),
    db: Session = Depends(get_db),
) -> dict:
    """Compliance trend — % compliant per time bucket over ``days``.

    Default ``bucket='week'`` for the 30-day view (4-5 points, readable).
    Day-buckets for shorter windows. Calls created within the window count.
    """
    now = utcnow()
    floor = now - timedelta(days=days)
    rows = (
        _completed(db)
        .filter(Call.created_at >= floor)
        .with_entities(
            Call.created_at,
            Call.compliant,
        )
        .all()
    )

    # Group server-side in Python — bucket math is dialect-fragile and the
    # window is small (≤180 days * ≤a few thousand calls).
    span_seconds = 7 * 86400 if bucket == "week" else 86400
    buckets: dict[int, dict[str, int]] = {}
    for r in rows:
        if not r.created_at:
            continue
        # Bucket index relative to `floor` so labels are stable per request.
        idx = int((r.created_at - floor).total_seconds() // span_seconds)
        slot = buckets.setdefault(idx, {"total": 0, "compliant": 0})
        slot["total"] += 1
        if r.compliant:
            slot["compliant"] += 1

    items = []
    for idx in sorted(buckets.keys()):
        slot = buckets[idx]
        label_dt = floor + timedelta(seconds=span_seconds * idx)
        label = label_dt.strftime("%Y-%m-%d") if bucket == "day" else f"Wk {label_dt.strftime('%m-%d')}"
        items.append(
            {
                "label": label,
                "total": slot["total"],
                "compliant": slot["compliant"],
                "compliance_pct": _pct(slot["compliant"], slot["total"]),
            }
        )
    return {"items": items, "bucket": bucket, "days": days}
