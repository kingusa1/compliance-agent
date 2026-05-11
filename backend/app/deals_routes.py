"""Deals API surface (Pillar 3 / L3).

Endpoints:
    POST   /api/deals                — create (existing)
    GET    /api/deals                — list with status / supplier / q
                                       filters + offset pagination
    GET    /api/deals/{id}           — detail (deal header + lifecycle
                                       + linked calls)
    GET    /api/deals/{id}/verdict   — rolled-up verdict (composite
                                       score + missing_calls + worst
                                       action) — see deal_verdict.py
    GET    /api/deals/{id}/calls     — child calls (existing)

Pagination is offset-based for now per L2 deferral note — cursor
pagination is a future-work item but offset is good enough for the
volumes we see in MVP.
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.auth import current_user
from app.database import get_db
from app.deal_lifecycle import derive_lifecycle_status
from app.deal_verdict import DealVerdict, aggregate_deal_verdict
from app.deals_composite import compute_composite_verdict
from app.models import Call, CustomerDeal
from app.schemas import CustomerDealCreate, CustomerDealOut

deals_router = APIRouter(prefix="/api/deals", tags=["deals"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _serialise_deal(deal: CustomerDeal) -> dict:
    """Compact dict shape returned by list/detail. Keeps the public
    response stable independent of the SQLAlchemy column set."""
    return {
        "id": str(deal.id),
        "customer_name": deal.customer_name,
        "supplier": deal.supplier,
        "status": deal.status,
        "deal_value_gbp": (
            float(deal.deal_value_gbp) if deal.deal_value_gbp is not None else None
        ),
        "mpan_or_mprn": deal.mpan_or_mprn,
        "expected_live_date": (
            deal.expected_live_date.isoformat() if deal.expected_live_date else None
        ),
        "final_score": (
            float(deal.final_score) if deal.final_score is not None else None
        ),
        "final_action": deal.final_action,
        "risk_tags": list(deal.risk_tags or []),
        "rejection_category": deal.rejection_category,
        "assigned_agent_id": deal.assigned_agent_id,
        "pipeline_workflow_id": deal.pipeline_workflow_id,
        "created_at": deal.created_at.isoformat() if deal.created_at else None,
        # L3: lifecycle_status is column-when-present (after migration);
        # otherwise derived live so the field is always populated.
        "lifecycle_status": getattr(deal, "lifecycle_status", None) or "open",
        # W1 (v3-watt-coverage): Watt portal deep-link integer (X1).
        "external_watt_site_id": getattr(deal, "external_watt_site_id", None),
        # W1 (v3-watt-coverage): meter array — additive over mpan_or_mprn (X2).
        "meters": list(getattr(deal, "meters", None) or []),
    }


def _serialise_call(c: Call) -> dict:
    return {
        "id": str(c.id),
        "deal_id": str(c.deal_id) if c.deal_id else None,
        "call_type": c.call_type,
        "filename": c.filename,
        "status": c.status,
        "score": c.score,
        "compliant": c.compliant,
        "compliance_status": c.compliance_status,
        "agent_name": c.agent_name,
        "customer_name": c.customer_name,
        "detected_supplier": c.detected_supplier,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "completed_at": c.completed_at.isoformat() if c.completed_at else None,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@deals_router.post("", response_model=CustomerDealOut, status_code=status.HTTP_201_CREATED)
def create_deal(
    payload: CustomerDealCreate,
    request: Request,
    db: Session = Depends(get_db),
) -> CustomerDealOut:
    deal = CustomerDeal(**payload.model_dump(exclude_unset=True))
    db.add(deal)
    # Flush to materialise the server-generated UUID before we capture it in
    # the audit row — record_audit() runs inside this transaction so the
    # business write + chain extension stay atomic on the same commit.
    db.flush()
    record_audit(
        db,
        action="deal.create",
        entity_type="deal",
        entity_id=str(deal.id),
        payload={
            "supplier": deal.supplier,
            "status": deal.status,
            "has_mpan_or_mprn": bool(deal.mpan_or_mprn),
            "meter_count": len(deal.meters or []),
            "external_watt_site_id": deal.external_watt_site_id,
        },
        actor_id=request.headers.get("x-user-id"),
    )
    db.commit()
    db.refresh(deal)
    return deal


@deals_router.get("")
def list_deals(
    status: Optional[str] = Query(None),
    supplier: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    """List deals with offset pagination + status / supplier / q
    filters. ``q`` does an ILIKE on customer_name."""
    query = db.query(CustomerDeal)
    if status:
        query = query.filter(CustomerDeal.status == status)
    if supplier:
        query = query.filter(CustomerDeal.supplier == supplier)
    if q:
        query = query.filter(CustomerDeal.customer_name.ilike(f"%{q}%"))

    total = query.count()
    rows = (
        query.order_by(CustomerDeal.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    # Compute lifecycle_status per row from the calls it owns. The stored
    # column defaults to "open"; only `get_deal` was deriving live, so the
    # listing always showed "open" for every row. (audit-late B6.) Bulk
    # the call lookup so this stays O(deals + calls), not O(deals × N).
    deal_ids = [r.id for r in rows]
    calls_by_deal: dict = {did: [] for did in deal_ids}
    if deal_ids:
        for c in (
            db.query(Call)
            .filter(Call.deal_id.in_(deal_ids))
            .order_by(Call.created_at.asc())
            .all()
        ):
            calls_by_deal.setdefault(c.deal_id, []).append(c)

    out_deals = []
    for r in rows:
        d = _serialise_deal(r)
        try:
            d["lifecycle_status"] = derive_lifecycle_status(r, calls_by_deal.get(r.id, []))
        except Exception:
            # Keep the stored value if derivation blows up — failure here
            # should never break the listing.
            pass
        out_deals.append(d)

    return {
        "deals": out_deals,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(rows) < total,
    }


@deals_router.get("/{deal_id}")
def get_deal(deal_id: UUID, db: Session = Depends(get_db)) -> dict:
    """Deal detail: header + lifecycle_status + linked calls. The
    page calls /verdict separately so we don't recompute the verdict
    on every header-only fetch."""
    deal = db.query(CustomerDeal).filter(CustomerDeal.id == deal_id).one_or_none()
    if not deal:
        raise HTTPException(404, "deal not found")

    calls = (
        db.query(Call)
        .filter(Call.deal_id == deal.id)
        .order_by(Call.created_at.asc())
        .all()
    )
    # Derive live so the response is correct even before the L3
    # migration lands the column. Once the column exists, the value
    # in _serialise_deal will reflect what the pipeline persisted.
    derived_lifecycle = derive_lifecycle_status(deal, calls)
    payload = _serialise_deal(deal)
    payload["lifecycle_status"] = derived_lifecycle

    # Response shape: deal fields are also spread at the root so older
    # callers that did `resp["id"]` keep working. Newer callers should
    # read `resp["deal"]` and `resp["calls"]`.
    return {
        **payload,
        "deal": payload,
        "calls": [_serialise_call(c) for c in calls],
    }


@deals_router.get("/{deal_id}/verdict", response_model=DealVerdict)
def get_deal_verdict(deal_id: UUID, db: Session = Depends(get_db)) -> DealVerdict:
    try:
        return aggregate_deal_verdict(deal_id, db)
    except ValueError as e:
        raise HTTPException(404, str(e))


@deals_router.get("/{deal_id}/composite-verdict")
def get_deal_composite_verdict(
    deal_id: UUID,
    db: Session = Depends(get_db),
    user: dict = Depends(current_user),
) -> dict:
    """Sprint Task B — composite Deal verdict (weighted-avg of call scores).

    Watt thinks per-Deal, not per-Call: this rolls call scores up to a
    single percentage + worst_action so /deals/[id] can show one donut.
    See `app.deals_composite` for the math + per-call-type weights.
    """
    return compute_composite_verdict(deal_id, db)


@deals_router.get("/{deal_id}/calls")
def get_deal_calls(deal_id: UUID, db: Session = Depends(get_db)) -> dict:
    deal = db.query(CustomerDeal).filter(CustomerDeal.id == deal_id).one_or_none()
    if not deal:
        raise HTTPException(404, "deal not found")
    rows = (
        db.query(Call)
        .filter(Call.deal_id == deal_id)
        .order_by(Call.created_at.desc())
        .all()
    )
    return {"calls": [_serialise_call(c) for c in rows]}
