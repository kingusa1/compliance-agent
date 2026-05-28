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

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.auth import current_user
from app.database import get_db
from app.reviewers import current_reviewer, require_lead
from app.deal_lifecycle import derive_lifecycle_status
from app.deal_verdict import DealVerdict, aggregate_deal_verdict
from app.deals_composite import compute_composite_verdict, composite_from_calls
from app.models import Call, CustomerDeal
from app.schemas import CustomerDealCreate, CustomerDealOut
from app.segment_chips import fetch_segments_by_call_ids

deals_router = APIRouter(prefix="/api/deals", tags=["deals"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _meter_display(deal: CustomerDeal) -> Optional[str]:
    """Wave-46 (2026-05-28) — coalesce the meter identifier across all
    three storage generations so the deal page never shows "—" when a
    meter actually exists.

    Three columns can hold a meter, written by different code paths:
      * ``mpan_or_mprn``    — legacy combined column (XLSX tracker import,
        old pipeline). Retained read-only per the model comment.
      * ``mpan_electricity`` / ``mprn_gas`` — L7 split columns. EVERY new
        write goes here: the intake upsert, the wave-42/43 backfill, and
        the deal-meter matcher's hard-key lookup all read/write these.
      * ``meters[]``        — Watt dual-fuel JSON array.

    Before this, ``_serialise_deal`` returned only ``mpan_or_mprn``, so a
    reviewer who typed an MPAN on the upload form (which lands in
    ``mpan_electricity``) saw "—" on the deal page even though the value
    was stored and the matcher could hard-key on it. Owner-reported as
    "the MPAN never shows". Prefer the explicit split columns; fall back
    to the legacy column, then the meters array.
    """
    parts: List[str] = []
    if deal.mpan_electricity:
        parts.append(str(deal.mpan_electricity))
    if deal.mprn_gas:
        parts.append(str(deal.mprn_gas))
    if parts:
        return " / ".join(parts)
    if deal.mpan_or_mprn:
        return str(deal.mpan_or_mprn)
    for m in (getattr(deal, "meters", None) or []):
        if isinstance(m, dict):
            v = m.get("mpan") or m.get("mprn")
            if v:
                return str(v)
    return None


def _meters_display(deal: CustomerDeal) -> list:
    """Wave-46 — the meters array shown on the deal page. When the JSON
    ``meters`` column is empty (the common case for non-Watt deals),
    synthesise a single row from the L7 split columns so the dual-fuel
    UI still renders the reviewer's typed MPAN/MPRN."""
    arr = list(getattr(deal, "meters", None) or [])
    if arr:
        return arr
    row: dict = {}
    if deal.mpan_electricity:
        row["mpan"] = str(deal.mpan_electricity)
    if deal.mprn_gas:
        row["mprn"] = str(deal.mprn_gas)
    return [row] if row else []


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
        # Wave-46 — coalesce across legacy + L7-split + meters-array so the
        # value the reviewer typed (which lands in mpan_electricity) shows.
        "mpan_or_mprn": _meter_display(deal),
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
        # Wave-46 — synthesise from the L7 split columns when the JSON
        # array is empty so the dual-fuel UI shows the typed MPAN/MPRN.
        "meters": _meters_display(deal),
    }


def _serialise_call(c: Call, segments: list | None = None) -> dict:
    """Serialise one Call row + (wave-26) its segment chips.

    `segments` is the bulk-loaded list for this call_id from
    fetch_segments_by_call_ids. Defaults to [] so legacy code paths
    that don't pre-fetch keep working — but those paths now surface
    a single-string call_type only, same as before wave-26.
    """
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
        # Wave-26 — multi-segment array. UIs render one pill per chip
        # instead of a single call_type. Empty list = legacy call w/o
        # CallSegment rows; UI falls back to call_type.
        "segments": [s.model_dump() for s in (segments or [])],
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@deals_router.post("", response_model=CustomerDealOut, status_code=status.HTTP_201_CREATED)
def create_deal(
    payload: CustomerDealCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_lead),
) -> CustomerDealOut:
    """Create a CustomerDeal row.

    2026-05-24 audit — was unauthenticated and trusted a client-supplied
    ``x-user-id`` header to stamp the audit chain's ``actor_id``, letting
    any anonymous caller (a) flood the deals table, (b) forge an
    arbitrary actor_id on the tamper-evident audit log. Now gated by
    ``require_lead`` and the actor is read from the verified JWT user.
    """
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
        actor_id=user.get("id") if isinstance(user, dict) else None,
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
    _user: dict = Depends(current_reviewer),
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

    # Wave-27 — segment-coverage chip strip. Bulk-fetch every detected
    # CallSegment.kind for every call across every deal on this page in
    # ONE round-trip via the wave-26 json_agg helper. Then group by deal.
    # Without this the Deals list showed a single lifecycle pill per row
    # and hid the fact that one Marsden Capital file already covers
    # pre_sales + verbal. Reusing wave-26's §0 research (agent
    # a50f03bffacc55da8 — 4 citations on json_agg + COALESCE).
    all_call_ids: list[str] = []
    for ccs in calls_by_deal.values():
        for cc in ccs:
            all_call_ids.append(str(cc.id))
    segs_by_call = (
        fetch_segments_by_call_ids(db, all_call_ids) if all_call_ids else {}
    )

    # Hoist canonical_order once — same const used per-row in the loop.
    _CANONICAL_ORDER = ["lead_gen", "pre_sales", "verbal", "loa"]
    _CANONICAL_SET = set(_CANONICAL_ORDER)

    out_deals = []
    for r in rows:
        d = _serialise_deal(r)
        deal_calls = calls_by_deal.get(r.id, [])
        try:
            d["lifecycle_status"] = derive_lifecycle_status(r, deal_calls)
        except Exception:
            # Keep the stored value if derivation blows up — failure here
            # should never break the listing.
            pass
        # Composite score for the listing's Score column. The `final_score`
        # column on customer_deals isn't written by the pipeline yet, so the
        # listing always showed "—" even when the deal had a scored call.
        # Compute the same weighted-avg the /deals/{id} detail page uses so
        # the column carries the same number reviewers see when they drill in.
        try:
            comp = composite_from_calls(r.id, deal_calls)
            d["composite_pct"] = comp["composite_pct"]
            d["calls_scored"] = comp["calls_scored"]
            d["calls_total"] = comp["calls_total"]
            d["worst_action"] = comp["worst_action"]
            d["threshold_met"] = comp["threshold_met"]
            if d.get("final_score") is None and comp["composite_pct"] is not None:
                d["final_score"] = comp["composite_pct"]
        except Exception:
            # Listing must never 500 — degrade silently if the composite
            # math throws and let the row render its stored "—".
            pass
        # Wave-27 — derive segments_coverage: ordered, deduped list of
        # every segment kind detected across all calls in this deal.
        # Order follows the canonical taxonomy
        # (lead_gen → pre_sales → verbal → loa) so the UI chip strip
        # renders left-to-right in pipeline order.
        coverage_set: set[str] = set()
        for cc in deal_calls:
            for chip in segs_by_call.get(str(cc.id), []):
                k = (chip.kind or "").lower()
                if k:
                    coverage_set.add(k)
        d["segments_coverage"] = [
            k for k in _CANONICAL_ORDER if k in coverage_set
        ] + sorted(coverage_set - _CANONICAL_SET)
        out_deals.append(d)

    return {
        "deals": out_deals,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(rows) < total,
    }


@deals_router.get("/{deal_id}")
def get_deal(
    deal_id: UUID,
    db: Session = Depends(get_db),
    _user: dict = Depends(current_reviewer),
) -> dict:
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

    # Wave-27 — same segments_coverage field as the list endpoint so
    # the deal-detail page can render the chip strip without an extra
    # /api/deals/{id}/calls round-trip.
    segs_by_call = (
        fetch_segments_by_call_ids(db, [str(c.id) for c in calls])
        if calls else {}
    )
    coverage_set: set[str] = set()
    for cc in calls:
        for chip in segs_by_call.get(str(cc.id), []):
            k = (chip.kind or "").lower()
            if k:
                coverage_set.add(k)
    canonical_order = ["lead_gen", "pre_sales", "verbal", "loa"]
    payload["segments_coverage"] = [
        k for k in canonical_order if k in coverage_set
    ] + sorted(coverage_set - set(canonical_order))

    # Response shape: deal fields are also spread at the root so older
    # callers that did `resp["id"]` keep working. Newer callers should
    # read `resp["deal"]` and `resp["calls"]`.
    return {
        **payload,
        "deal": payload,
        "calls": [_serialise_call(c) for c in calls],
    }


@deals_router.get("/{deal_id}/verdict", response_model=DealVerdict)
def get_deal_verdict(
    deal_id: UUID,
    db: Session = Depends(get_db),
    _user: dict = Depends(current_reviewer),
) -> DealVerdict:
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
def get_deal_calls(
    deal_id: UUID,
    db: Session = Depends(get_db),
    _user: dict = Depends(current_reviewer),
) -> dict:
    deal = db.query(CustomerDeal).filter(CustomerDeal.id == deal_id).one_or_none()
    if not deal:
        raise HTTPException(404, "deal not found")
    rows = (
        db.query(Call)
        .filter(Call.deal_id == deal_id)
        .order_by(Call.created_at.desc())
        .all()
    )
    # Wave-26 — bulk-load segments for every call in ONE round-trip.
    # Without this the UI flattens multi-segment files to a single
    # "verbal" pill and the deal page double-counts "required calls".
    segs_by_call = fetch_segments_by_call_ids(db, [str(c.id) for c in rows])
    return {
        "calls": [
            _serialise_call(c, segs_by_call.get(str(c.id), []))
            for c in rows
        ]
    }
