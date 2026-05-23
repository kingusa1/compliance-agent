"""Wave 2 (v3-watt-coverage): /rejections workflow endpoints.

Stage 4 of Watt's 41-step flow. Backed by ``rejections`` +
``rejection_audit_log`` (alembic ``b1d4f7e2c903_w2_rejections.py``).

Endpoints
---------
GET    /api/rejections?tab=&category=&search=&dead_reason=&offset=&limit=
GET    /api/rejections/{id}
POST   /api/rejections                              (admin-only)
PATCH  /api/rejections/{id}                         (accepts dead_reason)
DELETE /api/rejections/{id}                         (admin-only)
POST   /api/rejections/{id}/transition              status changes + notes
GET    /api/rejections/{id}/audit-log
GET    /api/rejections/dead-reasons                 W4.6 vocab + glosses
GET    /api/portal-batches?supplier=               W4.5 supplier-grouped FIXED
POST   /api/portal-batches/submit                   W4.5 batch-submit (admin)

Tab routing
-----------
    active  → status IN ('NOT_STARTED', 'IN_PROGRESS')
    fixed   → status IN ('FIXED', 'BATCHED_TO_PORTAL', 'SUBMITTED_TO_PORTAL',
                         'FIXED_AND_APPROVED')
    dead    → status = 'DEAD'
    archive → ALL — pagination handles size

Audit log
---------
- POST writes a "created" row.
- PATCH writes "updated" with from_status/to_status when status changed.
- /transition writes "transitioned" with notes.
- /transition sets ``resolved_at`` when the new status is FIXED_AND_APPROVED
  or DEAD (terminal).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from app._clock import utcnow
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import current_user
from app.database import get_db
from app.logger import log
from app.models import Profile, Rejection, RejectionAuditLog


rejections_router = APIRouter(tags=["rejections"])


# ── enum vocabularies — must mirror alembic b1d4f7e2c903 ────────────────

REJECTION_CATEGORIES = {
    "ADMIN_ERROR",
    "PROCESS_FAILURE",
    "VERBAL_SALES_ERROR",
    "COMPLIANCE_ISSUE",
    "COMPLIANCE_ERROR",
    "PRICING_ISSUE",
    "PRICING_ERROR",
    "DOCUSIGN_ERROR",
    "FAILED_CREDIT_CHECK",
}
REJECTION_STATUSES = {
    "NOT_STARTED",
    "IN_PROGRESS",
    "FIXED",
    "BATCHED_TO_PORTAL",
    "SUBMITTED_TO_PORTAL",
    "FIXED_AND_APPROVED",
    "DEAD",
}
REJECTION_OUTCOMES = {
    "FIXED_AND_SUBMITTED",
    "CUSTOMER_LOST",
    "CANCELLED",
    "NOT_RECOVERABLE",
    "RESIGNED_TO_OTHER_SUPPLIER",
}
REMEDIATION_ACTIONS = {
    "AMENDMENT_CALL",
    "CONFIRMATION_CALL",
    "NEW_LOA",
    "NEW_DOCUSIGN",
    "DD_MANDATE",
    "RESELL_TO_OTHER_SUPPLIER",
    "PRICE_RECHECK",
    "COT_CHANGE_OF_TENANCY",
    "CONTRACT_LENGTH_LIMIT",
    "MANUAL_ADMIN_SUBMISSION",
}

# W4.6 — dead-reason vocabulary. Keys are the wire values written to
# ``rejections.dead_reason``; one-line glosses render as filter-chip
# tooltips on the /rejections Dead tab. Added per migration
# ``c4g7i8m9n0o1_w4_dead_reasons.py``.
DEAD_REASONS: dict[str, str] = {
    "in_contract":   "Customer already locked into another supplier — can't switch.",
    "customer_debt": "Outstanding balance with prior supplier blocks transfer.",
    "wrong_owner":   "Caller wasn't authorised on the account (wrong account holder).",
    "bacs_rejected": "Direct-debit mandate rejected by the bank — repeated attempts failed.",
    "hung_up":       "Customer disengaged mid-call; consent never reached.",
}

TERMINAL_STATUSES = {"FIXED_AND_APPROVED", "DEAD"}

ACTIVE_STATUSES = {"NOT_STARTED", "IN_PROGRESS"}
FIXED_LIKE_STATUSES = {
    "FIXED",
    "BATCHED_TO_PORTAL",
    "SUBMITTED_TO_PORTAL",
    "FIXED_AND_APPROVED",
}


# ── helpers ─────────────────────────────────────────────────────────────


def require_admin(user: dict = Depends(current_user)) -> dict:
    """Admin-only gate for create + delete. Leads cannot create / delete
    rejections; they can patch + transition like reviewers."""
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return user


def _compute_deadline(rejected_at: datetime) -> datetime:
    """Mirror the Postgres ``GENERATED ALWAYS AS rejected_at + INTERVAL '2 days'``
    column on SQLite. Always called on insert + on any rejected_at update."""
    return rejected_at + timedelta(days=2)


def _resolve_customer_names(
    db: Session, call_ids: list[str]
) -> dict[str, str]:
    """Resolve a display name for every call_id, using a deterministic
    fallback chain so the rejections list / grouped view never renders
    a bare ``—`` when any source has the name.

    Lookup order, per call_id:
      1. Customer.legal_name via Call.deal_id → CustomerDeal.customer_id
      2. CustomerDeal.customer_name (in case the customer_id link is null)
      3. Call.customer_name (set directly by the pipeline's detect_names
         step before the deal-linker fires)

    Returns an empty mapping for any call_id that resolves to None at
    every step — callers should fall back to ``customer_slug`` or "—".
    """
    if not call_ids:
        return {}
    from app.models import CustomerDeal as _Deal, Customer as _Customer, Call as _Call

    out: dict[str, str] = {}
    rows = (
        db.query(
            _Call.id,
            _Call.customer_name,
            _Deal.customer_name,
            _Customer.legal_name,
        )
        .outerjoin(_Deal, _Deal.id == _Call.deal_id)
        .outerjoin(_Customer, _Customer.id == _Deal.customer_id)
        .filter(_Call.id.in_(call_ids))
        .all()
    )
    for cid, call_name, deal_name, legal_name in rows:
        # Customer.legal_name is the most authoritative; deal name is
        # the linker's choice; call.customer_name is the detect_names
        # output. First non-empty wins.
        name = legal_name or deal_name or call_name
        if name and str(name).strip():
            out[cid] = str(name).strip()
    return out


def _serialize(r: Rejection, customer_name: str | None = None) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "call_id": r.call_id,
        "customer_slug": r.customer_slug,
        # 2026-05-12: client feedback — surface customer_name on every
        # rejection row. Joined upstream from Call → CustomerDeal →
        # Customer; falls back to the customer_slug-derived display.
        "customer_name": customer_name or r.customer_slug or None,
        "external_watt_site_id": r.external_watt_site_id,
        "supplier": r.supplier,
        "sales_agent": r.sales_agent,
        "category": r.category,
        "rejection_reason": r.rejection_reason,
        "fix_required": r.fix_required,
        "fix_narrative": getattr(r, "fix_narrative", None),
        "fix_assignee_id": r.fix_assignee_id,
        "status": r.status,
        "outcome": r.outcome,
        "outcome_narrative": r.outcome_narrative,
        # W4.6 — dead_reason populated only when status=DEAD; tolerate
        # legacy rows without the column (getattr fallback).
        "dead_reason": getattr(r, "dead_reason", None),
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "rejected_at": r.rejected_at.isoformat() if r.rejected_at else None,
        "deadline": r.deadline.isoformat() if r.deadline else None,
        "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
        # AI/HUMAN provenance gate (legacy rows tolerated via getattr).
        "verdict_state": getattr(r, "verdict_state", None) or "AI_PENDING",
        "confirmed_by": getattr(r, "confirmed_by", None),
        "confirmed_at": (
            r.confirmed_at.isoformat()
            if getattr(r, "confirmed_at", None) else None
        ),
        # Source: 'reviewer' (manually-opened by the human reviewer in
        # the queue) or 'ai' (auto-created by the pipeline — disabled
        # post 2026-05-12 rebuild, but legacy rows may still carry it).
        "source": getattr(r, "source", None) or (
            "reviewer" if getattr(r, "confirmed_by", None) else "ai"
        ),
    }


def _serialize_audit(a: RejectionAuditLog) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "rejection_id": str(a.rejection_id),
        "actor_id": a.actor_id,
        "action": a.action,
        "from_status": a.from_status,
        "to_status": a.to_status,
        "notes": a.notes,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


def _validate_enum(value: str | None, allowed: set[str], field: str) -> None:
    if value is not None and value not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"{field} must be one of {sorted(allowed)} (got {value!r})",
        )


# ── pydantic payloads ────────────────────────────────────────────────────


class RejectionCreate(BaseModel):
    call_id: str | None = None
    customer_slug: str | None = None
    external_watt_site_id: int | None = None
    supplier: str | None = None
    sales_agent: str | None = None
    category: str
    rejection_reason: str = Field(min_length=1)
    fix_required: str | None = None
    fix_assignee_id: str | None = None
    rejected_at: datetime | None = None  # defaults to NOW server-side


class RejectionPatch(BaseModel):
    customer_slug: str | None = None
    supplier: str | None = None
    sales_agent: str | None = None
    category: str | None = None
    rejection_reason: str | None = None
    fix_required: str | None = None
    fix_narrative: str | None = None
    fix_assignee_id: str | None = None
    status: str | None = None
    outcome: str | None = None
    outcome_narrative: str | None = None
    # W4.6 — dead-reason classification. Validated against ``DEAD_REASONS``
    # in ``patch_rejection``; only meaningful when status=DEAD but we don't
    # block writing it on non-DEAD rows (the chip just doesn't render).
    dead_reason: str | None = None


class TransitionPayload(BaseModel):
    to_status: str
    notes: str | None = None


class BulkTransitionPayload(BaseModel):
    """2026-05-24 — flip many rejections to the same status in one trip.

    Powers the /rejections bulk-action bar + per-group "Mark all fixed".
    Cap is 500 ids per request — enough to clear the largest single-call
    group (49 today, headroom for growth) while keeping the transaction
    bounded so the planner can hold the row locks without contention.
    """
    rejection_ids: list[str] = Field(min_length=1, max_length=500)
    to_status: str
    notes: str | None = None


class PortalBatchSubmit(BaseModel):
    """W4.5 — admin-only batch submit. ``rejection_ids`` must all belong
    to ``supplier`` and currently sit in a FIXED-like status. The route
    flips each one to SUBMITTED_TO_PORTAL + writes an audit row + logs the
    portal submit."""
    supplier: str = Field(min_length=1)
    rejection_ids: list[str] = Field(min_length=1)


# ── routes ───────────────────────────────────────────────────────────────


@rejections_router.get("/api/rejections")
def list_rejections(
    tab: str = Query("active", regex="^(active|fixed|dead|archive)$"),
    category: str | None = None,
    search: str | None = None,
    dead_reason: str | None = Query(
        None,
        description="W4.6 — restrict the dead tab to one of DEAD_REASONS keys.",
    ),
    source: str = Query(
        "all",
        regex="^(reviewer|ai|all)$",
        description=(
            "2026-05-12 client feedback — Phase 4 contract is enforced at "
            "create-time (pipeline._step_finalize no longer auto-creates "
            "Rejection rows). This filter now defaults to 'all' so the "
            "rejections list shows everything in the DB; pass "
            "source=reviewer to surface only rows confirmed by a human "
            "(confirmed_by IS NOT NULL), or source=ai for legacy AI-auto."
        ),
    ),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    user: dict = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    q = db.query(Rejection)
    if tab == "active":
        q = q.filter(Rejection.status.in_(ACTIVE_STATUSES))
    elif tab == "fixed":
        q = q.filter(Rejection.status.in_(FIXED_LIKE_STATUSES))
    elif tab == "dead":
        q = q.filter(Rejection.status == "DEAD")
    # archive: no status filter

    if category:
        _validate_enum(category, REJECTION_CATEGORIES, "category")
        q = q.filter(Rejection.category == category)

    if dead_reason:
        _validate_enum(dead_reason, set(DEAD_REASONS.keys()), "dead_reason")
        q = q.filter(Rejection.dead_reason == dead_reason)

    if search:
        like = f"%{search}%"
        q = q.filter(
            (Rejection.rejection_reason.ilike(like))
            | (Rejection.supplier.ilike(like))
            | (Rejection.customer_slug.ilike(like))
            | (Rejection.sales_agent.ilike(like))
        )

    # 2026-05-12 client feedback: Phase 4 stopped auto-creating Rejection
    # rows in pipeline._step_finalize. So the in-DB population is now
    # reviewer-initiated by construction. The filter below is opt-in:
    # reviewer-only callers pass ?source=reviewer (confirmed_by IS NOT
    # NULL); ?source=ai narrows to legacy AI-auto rows still in the DB.
    if source == "reviewer":
        q = q.filter(Rejection.confirmed_by.isnot(None))
    elif source == "ai":
        q = q.filter(Rejection.confirmed_by.is_(None))

    total = q.count()
    rows = (
        q.order_by(Rejection.rejected_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    # 2026-05-23 — use the shared customer-name resolver so the list
    # falls through Customer → CustomerDeal → Call instead of just the
    # Customer join (which was returning null for auto-created
    # rejections where the deal had no customer_id).
    call_ids = [r.call_id for r in rows if r.call_id]
    customer_name_by_id = _resolve_customer_names(db, call_ids)

    # Per-tab counts (always over the same base set, no other filters) so
    # the top-bar tabs can render a reliable badge regardless of which tab
    # is currently filtered.
    base = db.query(Rejection)
    counts = {
        "active": base.filter(Rejection.status.in_(ACTIVE_STATUSES)).count(),
        "fixed": base.filter(Rejection.status.in_(FIXED_LIKE_STATUSES)).count(),
        "dead": base.filter(Rejection.status == "DEAD").count(),
        "archive": base.count(),
    }
    return {
        "rejections": [
            _serialize(r, customer_name=customer_name_by_id.get(r.call_id))
            for r in rows
        ],
        "total": total,
        "counts": counts,
        "tab": tab,
        "limit": limit,
        "offset": offset,
    }


@rejections_router.get("/api/rejections/grouped")
def list_rejections_grouped(
    tab: str = Query("active", regex="^(active|fixed|dead|archive)$"),
    category: str | None = None,
    search: str | None = None,
    dead_reason: str | None = Query(None),
    source: str = Query("all", regex="^(reviewer|ai|all)$"),
    limit: int = Query(100, ge=1, le=500),
    user: dict = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Group rejections by call so the /rejections page renders one
    card per call instead of N cards per call.

    Designed for the 2026-05-23 redesign: a single submitted verdict
    produces 1 Rejection row per failing checkpoint, which means a
    standard non-compliant call generates 30-50 rows in the list view.
    The reviewer cares about the CALL — not each checkpoint failure in
    isolation — so this endpoint pre-aggregates them.

    Response shape::

        {
          "groups": [
            {
              "call_id": "...",
              "customer_name": "Baba",            # via _resolve_customer_names
              "agent_name": "Paige",
              "supplier": "E.ON Next",
              "score": "39/88",
              "call_type": "lead_gen",
              "rejection_count": 32,
              "status_mix": {"NOT_STARTED": 32},  # how many in each status
              "category_mix": {"COMPLIANCE_ISSUE": 12, ...},
              "oldest_deadline": "2026-05-25T00:00:00",
              "first_rejected_at": "2026-05-23T...",
              "rejections": [ ...full _serialize() shape per row... ]
            },
            ...
          ],
          "total_groups": 7,
          "total_rejections": 49,
          "counts": {active, fixed, dead, archive}  # same per-call counts as list endpoint
          "tab": "active"
        }

    Filtering semantics match ``list_rejections`` exactly so the same
    Category / Search / Source chips drive both views. Sort: most-
    rejected call first, then oldest_deadline ascending.
    """
    q = db.query(Rejection)
    if tab == "active":
        q = q.filter(Rejection.status.in_(ACTIVE_STATUSES))
    elif tab == "fixed":
        q = q.filter(Rejection.status.in_(FIXED_LIKE_STATUSES))
    elif tab == "dead":
        q = q.filter(Rejection.status == "DEAD")

    if category:
        _validate_enum(category, REJECTION_CATEGORIES, "category")
        q = q.filter(Rejection.category == category)
    if dead_reason:
        _validate_enum(dead_reason, set(DEAD_REASONS.keys()), "dead_reason")
        q = q.filter(Rejection.dead_reason == dead_reason)
    if search:
        like = f"%{search}%"
        q = q.filter(
            (Rejection.rejection_reason.ilike(like))
            | (Rejection.supplier.ilike(like))
            | (Rejection.customer_slug.ilike(like))
            | (Rejection.sales_agent.ilike(like))
        )
    if source == "reviewer":
        q = q.filter(Rejection.confirmed_by.isnot(None))
    elif source == "ai":
        q = q.filter(Rejection.confirmed_by.is_(None))

    # 2026-05-23: orphan guard — only group rejections with a call_id.
    # Detached rejections (no parent call) are surfaced separately by
    # the flat list endpoint; they don't belong in the grouped view.
    q = q.filter(Rejection.call_id.isnot(None))
    rows = q.order_by(Rejection.rejected_at.desc()).limit(limit * 50).all()

    # Resolve customer + call metadata in one bulk trip per the LAW's
    # no-N+1 rule.
    call_ids = list({r.call_id for r in rows if r.call_id})
    customer_name_by_id = _resolve_customer_names(db, call_ids)

    # Pull call metadata (agent_name, supplier, score, call_type) once.
    from app.models import Call as _Call
    call_meta_by_id: dict[str, dict[str, Any]] = {}
    if call_ids:
        for c in (
            db.query(
                _Call.id, _Call.agent_name, _Call.detected_supplier,
                _Call.score, _Call.call_type, _Call.compliance_status,
                _Call.review_status, _Call.completed_at,
            )
            .filter(_Call.id.in_(call_ids))
            .all()
        ):
            call_meta_by_id[c.id] = {
                "agent_name": c.agent_name,
                "supplier": c.detected_supplier,
                "score": c.score,
                "call_type": c.call_type,
                "compliance_status": c.compliance_status,
                "review_status": c.review_status,
                "completed_at": c.completed_at.isoformat() if c.completed_at else None,
            }

    # Group in Python — small N (max ~500 rejection rows per call after
    # the limit fence). Each group's `rejections` list preserves
    # insertion order so the frontend renders chronologically without
    # re-sorting.
    from collections import OrderedDict
    groups: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
    for r in rows:
        cid = r.call_id
        if cid is None:
            continue
        g = groups.get(cid)
        if g is None:
            meta = call_meta_by_id.get(cid, {})
            g = {
                "call_id": cid,
                "customer_name": (
                    customer_name_by_id.get(cid)
                    or r.customer_slug
                    or None
                ),
                "customer_slug": r.customer_slug,
                "agent_name": meta.get("agent_name") or r.sales_agent,
                "supplier": meta.get("supplier") or r.supplier,
                "score": meta.get("score"),
                "call_type": meta.get("call_type"),
                "compliance_status": meta.get("compliance_status"),
                "review_status": meta.get("review_status"),
                "completed_at": meta.get("completed_at"),
                "external_watt_site_id": r.external_watt_site_id,
                "rejection_count": 0,
                "status_mix": {},
                "category_mix": {},
                "oldest_deadline": None,
                "first_rejected_at": None,
                "rejections": [],
            }
            groups[cid] = g

        # Append serialized rejection.
        g["rejections"].append(
            _serialize(r, customer_name=customer_name_by_id.get(cid))
        )
        g["rejection_count"] = len(g["rejections"])

        # Update status + category mix counts.
        status = r.status or "UNKNOWN"
        g["status_mix"][status] = g["status_mix"].get(status, 0) + 1
        if r.category:
            g["category_mix"][r.category] = g["category_mix"].get(r.category, 0) + 1

        # Track oldest deadline (smallest = most urgent) + first rejection.
        if r.deadline is not None:
            iso = r.deadline.isoformat()
            if g["oldest_deadline"] is None or iso < g["oldest_deadline"]:
                g["oldest_deadline"] = iso
        if r.rejected_at is not None:
            iso = r.rejected_at.isoformat()
            if g["first_rejected_at"] is None or iso < g["first_rejected_at"]:
                g["first_rejected_at"] = iso

    # Sort: most rejections first (worst calls bubble up); tiebreak
    # by oldest_deadline ascending so deadline pressure breaks ties.
    sorted_groups = sorted(
        groups.values(),
        key=lambda x: (
            -x["rejection_count"],
            x["oldest_deadline"] or "9999",
        ),
    )[:limit]

    # Per-tab counts mirror the flat endpoint so the existing tab badges
    # keep working — counting CALLS (distinct call_id), not Rejection rows.
    base = db.query(Rejection).filter(Rejection.call_id.isnot(None))
    counts = {
        "active": base.filter(Rejection.status.in_(ACTIVE_STATUSES))
        .with_entities(Rejection.call_id).distinct().count(),
        "fixed": base.filter(Rejection.status.in_(FIXED_LIKE_STATUSES))
        .with_entities(Rejection.call_id).distinct().count(),
        "dead": base.filter(Rejection.status == "DEAD")
        .with_entities(Rejection.call_id).distinct().count(),
        "archive": base.with_entities(Rejection.call_id).distinct().count(),
    }

    return {
        "groups": sorted_groups,
        "total_groups": len(sorted_groups),
        "total_rejections": sum(g["rejection_count"] for g in sorted_groups),
        "counts": counts,
        "tab": tab,
    }


@rejections_router.get("/api/rejections/dead-reasons")
def list_dead_reasons(
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    """W4.6 — return the static dead-reason vocabulary + glosses so the
    frontend can render the Dead-tab filter chips with hover tooltips
    without hard-coding the list in two places. Mounted before
    ``/api/rejections/{rid}`` so the path doesn't get shadowed by the
    UUID-typed catch-all."""
    return {
        "dead_reasons": [
            {"key": k, "label": k.replace("_", " ").title(), "gloss": gloss}
            for k, gloss in DEAD_REASONS.items()
        ]
    }


@rejections_router.get("/api/rejections/{rid}")
def get_rejection(
    rid: UUID,
    user: dict = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    r = db.query(Rejection).filter(Rejection.id == rid).first()
    if not r:
        raise HTTPException(status_code=404, detail="Rejection not found")
    return _serialize(r)


@rejections_router.post("/api/rejections", status_code=201)
def create_rejection(
    payload: RejectionCreate,
    user: dict = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _validate_enum(payload.category, REJECTION_CATEGORIES, "category")
    _validate_enum(payload.fix_required, REMEDIATION_ACTIONS, "fix_required")

    rejected_at = payload.rejected_at or utcnow()
    rid = uuid.uuid4()
    r = Rejection(
        id=rid,
        call_id=payload.call_id,
        customer_slug=payload.customer_slug,
        external_watt_site_id=payload.external_watt_site_id,
        supplier=payload.supplier,
        sales_agent=payload.sales_agent,
        category=payload.category,
        rejection_reason=payload.rejection_reason,
        fix_required=payload.fix_required,
        fix_assignee_id=payload.fix_assignee_id,
        status="NOT_STARTED",
        rejected_at=rejected_at,
        deadline=_compute_deadline(rejected_at),
        created_at=utcnow(),
    )
    db.add(r)
    db.flush()

    db.add(
        RejectionAuditLog(
            id=uuid.uuid4(),
            rejection_id=rid,
            actor_id=user["id"],
            action="created",
            from_status=None,
            to_status="NOT_STARTED",
            notes=None,
            created_at=utcnow(),
        )
    )
    db.commit()
    db.refresh(r)
    log.info(f"REJECTION_CREATED id={rid} category={payload.category} actor={user['id']}")
    return _serialize(r)


@rejections_router.patch("/api/rejections/{rid}")
def patch_rejection(
    rid: UUID,
    payload: RejectionPatch,
    user: dict = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    r = db.query(Rejection).filter(Rejection.id == rid).first()
    if not r:
        raise HTTPException(status_code=404, detail="Rejection not found")

    _validate_enum(payload.category, REJECTION_CATEGORIES, "category")
    _validate_enum(payload.status, REJECTION_STATUSES, "status")
    _validate_enum(payload.outcome, REJECTION_OUTCOMES, "outcome")
    _validate_enum(payload.fix_required, REMEDIATION_ACTIONS, "fix_required")
    _validate_enum(payload.dead_reason, set(DEAD_REASONS.keys()), "dead_reason")

    prev_status = r.status
    changes = payload.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(r, k, v)

    # Set resolved_at if status moved to a terminal value via patch.
    if payload.status and payload.status in TERMINAL_STATUSES and r.resolved_at is None:
        r.resolved_at = utcnow()

    # Audit row for any patch — capture status delta if there was one.
    if payload.status and payload.status != prev_status:
        db.add(
            RejectionAuditLog(
                id=uuid.uuid4(),
                rejection_id=r.id,
                actor_id=user["id"],
                action="updated",
                from_status=prev_status,
                to_status=payload.status,
                notes=payload.outcome_narrative,
                created_at=utcnow(),
            )
        )
    elif changes:
        # Non-status patch — still log the touch so the timeline stays useful.
        db.add(
            RejectionAuditLog(
                id=uuid.uuid4(),
                rejection_id=r.id,
                actor_id=user["id"],
                action="updated",
                from_status=prev_status,
                to_status=prev_status,
                notes=", ".join(sorted(changes.keys())),
                created_at=utcnow(),
            )
        )

    db.commit()
    db.refresh(r)

    # Inngest observability — surface every rejection patch (status flip
    # or otherwise) so the dashboard can audit reviewer activity.
    try:
        from app.workflows.events import REJECTION_STATUS_CHANGED
        from app.workflows.observability import emit_event
        if payload.status and payload.status != prev_status:
            emit_event(REJECTION_STATUS_CHANGED, {
                "rejection_id": str(r.id),
                "from_status": prev_status,
                "to_status": payload.status,
                "actor_id": user["id"],
                "dead_reason": payload.dead_reason,
                "outcome": payload.outcome,
            })
    except Exception:
        pass

    return _serialize(r)


@rejections_router.delete("/api/rejections/{rid}")
def delete_rejection(
    rid: UUID,
    user: dict = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    r = db.query(Rejection).filter(Rejection.id == rid).first()
    if not r:
        raise HTTPException(status_code=404, detail="Rejection not found")
    db.delete(r)
    db.commit()
    return {"deleted": True, "id": str(rid)}


# ── AI/HUMAN verdict gate ────────────────────────────────────────────────
# verdict_state on each rejection starts as AI_PENDING after the
# rejection_factory writes it. A reviewer either confirms the AI verdict
# as-is (HUMAN_CONFIRMED) or edits a field and saves (HUMAN_OVERRIDDEN).
# Compliant/non-compliant pages exclude AI_PENDING — only human-touched
# rejections count toward those totals.

class _OverridePayload(BaseModel):
    category: str | None = None
    fix_required: str | None = None
    fix_narrative: str | None = None
    rejection_reason: str | None = None
    outcome_narrative: str | None = None


@rejections_router.post("/api/rejections/{rid}/confirm")
def confirm_verdict(
    rid: UUID,
    user: dict = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    r = db.query(Rejection).filter(Rejection.id == rid).first()
    if not r:
        raise HTTPException(status_code=404, detail="Rejection not found")

    prev_state = r.verdict_state
    r.verdict_state = "HUMAN_CONFIRMED"
    r.confirmed_by = user["id"]
    r.confirmed_at = utcnow()

    db.add(
        RejectionAuditLog(
            id=uuid.uuid4(),
            rejection_id=r.id,
            actor_id=user["id"],
            action="verdict_confirmed",
            from_status=prev_state,
            to_status="HUMAN_CONFIRMED",
            notes=None,
            created_at=utcnow(),
        )
    )
    db.commit()
    db.refresh(r)
    return _serialize(r)


@rejections_router.post("/api/rejections/{rid}/override")
def override_verdict(
    rid: UUID,
    payload: _OverridePayload,
    user: dict = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    r = db.query(Rejection).filter(Rejection.id == rid).first()
    if not r:
        raise HTTPException(status_code=404, detail="Rejection not found")

    _validate_enum(payload.category, REJECTION_CATEGORIES, "category")
    _validate_enum(payload.fix_required, REMEDIATION_ACTIONS, "fix_required")

    prev_state = r.verdict_state
    changes = payload.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(r, k, v)
    r.verdict_state = "HUMAN_OVERRIDDEN"
    r.confirmed_by = user["id"]
    r.confirmed_at = utcnow()

    db.add(
        RejectionAuditLog(
            id=uuid.uuid4(),
            rejection_id=r.id,
            actor_id=user["id"],
            action="verdict_overridden",
            from_status=prev_state,
            to_status="HUMAN_OVERRIDDEN",
            notes=", ".join(sorted(changes.keys())) if changes else None,
            created_at=utcnow(),
        )
    )
    db.commit()
    db.refresh(r)
    return _serialize(r)


@rejections_router.post("/api/rejections/{rid}/transition")
def transition_rejection(
    rid: UUID,
    payload: TransitionPayload,
    user: dict = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    r = db.query(Rejection).filter(Rejection.id == rid).first()
    if not r:
        raise HTTPException(status_code=404, detail="Rejection not found")

    _validate_enum(payload.to_status, REJECTION_STATUSES, "to_status")

    prev_status = r.status
    r.status = payload.to_status
    if payload.to_status in TERMINAL_STATUSES and r.resolved_at is None:
        r.resolved_at = utcnow()

    db.add(
        RejectionAuditLog(
            id=uuid.uuid4(),
            rejection_id=r.id,
            actor_id=user["id"],
            action="transitioned",
            from_status=prev_status,
            to_status=payload.to_status,
            notes=payload.notes,
            created_at=utcnow(),
        )
    )
    db.commit()
    db.refresh(r)
    return _serialize(r)


@rejections_router.post("/api/rejections/bulk-transition")
def bulk_transition_rejections(
    payload: BulkTransitionPayload,
    user: dict = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Flip many rejections to one status in a single transaction.

    Used by the /rejections bulk-action bar + the per-group "Mark all
    fixed" button on RejectionGroupCard. The per-id PATCH does the same
    job one row at a time; this endpoint exists so the UI can clear a
    49-row group with one request + one audit batch instead of 49
    chatty round-trips.

    Idempotent: rejections already in ``to_status`` are reported back
    under ``ids_skipped`` instead of being re-written. Re-sending the
    same payload twice produces the same end state with zero double
    audit rows. Frontend can blind-retry on network errors.

    Authorization: reviewer or higher (same gate as PATCH). Admin role
    is not required — bulk-fixing is a routine reviewer action.
    """
    _validate_enum(payload.to_status, REJECTION_STATUSES, "to_status")

    # De-dupe server-side; React Strict-Mode + retry races can resubmit
    # the same id twice within one request without the client meaning
    # to. Empty strings drop out so a stray "" from the UI doesn't 404.
    ids = list({rid for rid in payload.rejection_ids if rid})
    if not ids:
        raise HTTPException(
            status_code=400, detail="rejection_ids must contain at least one id"
        )

    # Coerce to UUID so the `in_()` filter binds correctly under both
    # Postgres (PGUUID column) and the SQLite test engine. Malformed
    # strings surface as ids_not_found instead of 400 — the bulk call
    # may receive a stale id from a slow client and we'd rather report
    # it back than reject the whole batch.
    rejection_uuids: list[UUID] = []
    bad_ids: list[str] = []
    for raw in ids:
        try:
            rejection_uuids.append(UUID(raw))
        except (ValueError, TypeError):
            bad_ids.append(raw)

    rows = (
        db.query(Rejection).filter(Rejection.id.in_(rejection_uuids)).all()
        if rejection_uuids
        else []
    )
    found_ids = {str(r.id) for r in rows}
    ids_not_found = [rid for rid in ids if rid not in found_ids]

    now = utcnow()
    ids_updated: list[str] = []
    ids_skipped: list[str] = []

    for r in rows:
        if r.status == payload.to_status:
            ids_skipped.append(str(r.id))
            continue
        prev_status = r.status
        r.status = payload.to_status
        if payload.to_status in TERMINAL_STATUSES and r.resolved_at is None:
            r.resolved_at = now
        db.add(
            RejectionAuditLog(
                id=uuid.uuid4(),
                rejection_id=r.id,
                actor_id=user["id"],
                action="bulk_transitioned",
                from_status=prev_status,
                to_status=payload.to_status,
                notes=payload.notes,
                created_at=now,
            )
        )
        ids_updated.append(str(r.id))

    db.commit()

    # Inngest fan-out so the realtime layer (and any downstream
    # observability) sees a per-row event, not one aggregate. The
    # /rejections + /tracker pages both invalidate on these.
    try:
        from app.workflows.events import REJECTION_STATUS_CHANGED
        from app.workflows.observability import emit_event
        for rid in ids_updated:
            emit_event(REJECTION_STATUS_CHANGED, {
                "rejection_id": rid,
                "to_status": payload.to_status,
                "actor_id": user["id"],
                "bulk": True,
                "batch_size": len(ids_updated),
            })
    except Exception:
        # Log + continue: a flaky event sidecar must not fail the user
        # action that already committed to the DB. Per LAW §5 we log
        # the exception with context instead of swallowing it silently.
        log.exception("bulk_transition_event_emit_failed")

    log.info(
        "REJECTION_BULK_TRANSITIONED "
        f"actor={user['id']} to_status={payload.to_status} "
        f"updated={len(ids_updated)} skipped={len(ids_skipped)} "
        f"not_found={len(ids_not_found)}"
    )

    return {
        "updated": len(ids_updated),
        "skipped_already_in_state": len(ids_skipped),
        "not_found": len(ids_not_found),
        "ids_updated": ids_updated,
        "ids_skipped": ids_skipped,
        "ids_not_found": ids_not_found,
        "to_status": payload.to_status,
    }


@rejections_router.get("/api/rejections/{rid}/audit-log")
def list_audit_log(
    rid: UUID,
    user: dict = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    r = db.query(Rejection).filter(Rejection.id == rid).first()
    if not r:
        raise HTTPException(status_code=404, detail="Rejection not found")
    rows = (
        db.query(RejectionAuditLog)
        .filter(RejectionAuditLog.rejection_id == r.id)
        .order_by(RejectionAuditLog.created_at.desc())
        .all()
    )
    return {"audit_log": [_serialize_audit(a) for a in rows]}


# ── W4.5 — portal-batches admin endpoints ────────────────────────────────


@rejections_router.get("/api/portal-batches")
def list_portal_batches(
    supplier: str | None = None,
    user: dict = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Group FIXED rejections by supplier so the admin team can submit
    them to each supplier's portal in one batch. ``FIXED`` here means any
    status in {FIXED, BATCHED_TO_PORTAL} — once a row hits SUBMITTED or
    APPROVED it falls out (already gone over the wire).

    ``supplier=`` narrows to a single bucket; otherwise we return every
    supplier with at least one fixed rejection."""
    batchable_statuses = {"FIXED", "BATCHED_TO_PORTAL"}
    q = db.query(Rejection).filter(Rejection.status.in_(batchable_statuses))
    if supplier:
        q = q.filter(Rejection.supplier == supplier)
    rows = q.order_by(Rejection.supplier, Rejection.rejected_at.asc()).all()

    by_supplier: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        sup = r.supplier or "(unknown supplier)"
        by_supplier.setdefault(sup, []).append(
            {
                "id": str(r.id),
                "customer_name": r.customer_slug,  # frontend treats as display label
                "customer_slug": r.customer_slug,
                "external_watt_site_id": r.external_watt_site_id,
                "rejection_reason": r.rejection_reason,
                "category": r.category,
                "status": r.status,
                "fixed_at": (
                    r.resolved_at.isoformat() if r.resolved_at else None
                ),
            }
        )

    batches = [
        {"supplier": sup, "count": len(items), "rejections": items}
        for sup, items in sorted(by_supplier.items())
    ]
    return {"batches": batches}


@rejections_router.post("/api/portal-batches/submit")
def submit_portal_batch(
    payload: PortalBatchSubmit,
    user: dict = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Flip every rejection in ``rejection_ids`` to SUBMITTED_TO_PORTAL +
    write an audit row + log the (stubbed) outbound portal call.

    Validation:
      - all ids must exist
      - all rows must currently be in {FIXED, BATCHED_TO_PORTAL}
      - all rows' supplier must match ``payload.supplier`` (so we don't
        accidentally submit an E.ON row to the BGL portal)
    """
    rejection_uuids: list[UUID] = []
    for raw in payload.rejection_ids:
        try:
            rejection_uuids.append(UUID(raw))
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid rejection id: {raw!r}",
            )

    rows = (
        db.query(Rejection)
        .filter(Rejection.id.in_(rejection_uuids))
        .all()
    )
    found_ids = {str(r.id) for r in rows}
    missing = [rid for rid in payload.rejection_ids if rid not in found_ids]
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"Rejection(s) not found: {missing}",
        )

    batchable_statuses = {"FIXED", "BATCHED_TO_PORTAL"}
    bad_status = [r for r in rows if r.status not in batchable_statuses]
    if bad_status:
        raise HTTPException(
            status_code=400,
            detail=(
                "Some rejections are not in a submittable state "
                f"(must be FIXED or BATCHED_TO_PORTAL): "
                f"{[(str(r.id), r.status) for r in bad_status]}"
            ),
        )

    bad_supplier = [r for r in rows if (r.supplier or "") != payload.supplier]
    if bad_supplier:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Some rejections don't belong to supplier {payload.supplier!r}: "
                f"{[(str(r.id), r.supplier) for r in bad_supplier]}"
            ),
        )

    # ── stubbed portal API call ────────────────────────────────────────
    # In production this would POST to the supplier's portal endpoint
    # (per supplier-specific adapter). Today we just emit a structured
    # log line so the operator can verify the batch shipped.
    log.info(
        f"PORTAL_BATCH_SUBMIT supplier={payload.supplier} "
        f"count={len(rows)} actor={user['id']} "
        f"ids={[str(r.id) for r in rows]}"
    )

    submitted_at = utcnow()
    for r in rows:
        prev = r.status
        r.status = "SUBMITTED_TO_PORTAL"
        db.add(
            RejectionAuditLog(
                id=uuid.uuid4(),
                rejection_id=r.id,
                actor_id=user["id"],
                action="portal_submitted",
                from_status=prev,
                to_status="SUBMITTED_TO_PORTAL",
                notes=f"Batched to {payload.supplier} portal",
                created_at=submitted_at,
            )
        )

    db.commit()

    # Inngest observability — surface batch submit + per-rejection
    # status flip in the dashboard.
    try:
        from app.workflows.events import PORTAL_BATCH_SUBMITTED, REJECTION_STATUS_CHANGED
        from app.workflows.observability import emit_event
        rejection_ids = [str(r.id) for r in rows]
        emit_event(PORTAL_BATCH_SUBMITTED, {
            "supplier": payload.supplier,
            "rejection_ids": rejection_ids,
            "submitted_count": len(rows),
            "actor_id": user["id"],
        })
        for r in rows:
            emit_event(REJECTION_STATUS_CHANGED, {
                "rejection_id": str(r.id),
                "from_status": "FIXED",
                "to_status": "SUBMITTED_TO_PORTAL",
                "actor_id": user["id"],
            })
    except Exception:
        pass

    return {
        "submitted": len(rows),
        "supplier": payload.supplier,
        "rejection_ids": [str(r.id) for r in rows],
    }


# ── auto-create on FAIL/REVIEW verdict (called from hitl_routes) ─────────

# Keyword scan: keep order-stable so two equal-priority matches resolve
# deterministically. Most specific keywords first.
_CATEGORY_RULES: list[tuple[set[str], str]] = [
    ({"vat", "ccl", "green deal"}, "COMPLIANCE_ERROR"),
    (
        {"compliance", "broker", "disclaimer", "watt", "ombudsman", "tpi"},
        "COMPLIANCE_ISSUE",
    ),
    ({"pricing", "rate", "uplift", "tariff", "price"}, "PRICING_ISSUE"),
    (
        {"missed", "didn't say", "did not say", "not stated", "missing"},
        "VERBAL_SALES_ERROR",
    ),
    (
        {"bacs", "dd", "credit", "in contract", "in-contract", "expired", "envelope"},
        "PROCESS_FAILURE",
    ),
    ({"docusign"}, "DOCUSIGN_ERROR"),
    ({"name", "address", "mpan", "mprn", "wrong", "typo"}, "ADMIN_ERROR"),
]


def infer_category(reason: str | None, rule_id: str | None = None) -> str:
    """Heuristic category inference for auto-created rejections.

    Scans the reviewer's free-text reason + the firing rule_id (if any)
    for keywords. Falls back to ADMIN_ERROR — the safest default in the
    XLSX deep-dive (tracker rows tagged ADMIN_ERROR are the largest
    bucket and rarely need to be re-categorized).
    """
    haystack = " ".join(filter(None, [reason or "", rule_id or ""])).lower()
    for keywords, cat in _CATEGORY_RULES:
        for kw in keywords:
            if kw in haystack:
                return cat
    return "ADMIN_ERROR"


# W4.7 — minimum AI confidence to prefer the suggested category over the
# keyword heuristic. Picked from the bucket-accuracy benchmark (XLSX
# deep-dive review): at ≥0.7 the AI is right ~91% of the time vs the
# heuristic's ~50%. Below this we fall back so a low-confidence guess
# never overwrites the safer "ADMIN_ERROR" default.
AI_CATEGORY_MIN_CONFIDENCE = 0.7


def _resolve_ai_suggestion(
    cp: dict | object | None,
) -> tuple[str | None, str | None, float | None]:
    """Pull (category, fix_required, confidence) off either:
      - a JSON checkpoint result dict (from ``call.checkpoint_results`` —
        keys ``suggested_category`` / ``suggested_fix_required`` /
        ``category_confidence``, the analyzer's output schema), OR
      - a CallCheckpoint ORM row (W4.7 columns
        ``ai_category`` / ``ai_fix_required`` / ``ai_category_confidence``).

    Validates against the Watt enums; mistyped or out-of-vocab values
    collapse to None so the caller's ``conf >= 0.7`` gate naturally falls
    through to the heuristic without bespoke branching.
    """
    if cp is None:
        return None, None, None

    if isinstance(cp, dict):
        cat = cp.get("suggested_category") or cp.get("ai_category")
        fix = cp.get("suggested_fix_required") or cp.get("ai_fix_required")
        conf = cp.get("category_confidence")
        if conf is None:
            conf = cp.get("ai_category_confidence")
    else:
        cat = getattr(cp, "ai_category", None)
        fix = getattr(cp, "ai_fix_required", None)
        conf = getattr(cp, "ai_category_confidence", None)

    if not isinstance(cat, str) or cat not in REJECTION_CATEGORIES:
        cat = None
    if not isinstance(fix, str) or fix not in REMEDIATION_ACTIONS:
        fix = None
    if isinstance(conf, bool) or not isinstance(conf, (int, float)):
        conf = None
    elif not (0.0 <= float(conf) <= 1.0):
        conf = None
    else:
        conf = float(conf)
    if cat is None:
        # Confidence without a category is meaningless — discard.
        conf = None
    return cat, fix, conf


def auto_create_rejection_for_verdict(
    db: Session,
    *,
    call,
    actor_id: str,
    verdict_action: str,
    reason: str | None,
    rule_id: str | None = None,
    checkpoint: dict | object | None = None,
) -> Rejection | None:
    """Side-effect helper invoked by submit_verdict when a reviewer marks a
    checkpoint FAIL or REVIEW. Creates a rejection + audit log row.

    Returns the created Rejection (so the caller can surface its id in the
    response), or None if `verdict_action` doesn't trigger creation.

    W4.7 — when ``checkpoint`` carries an AI-suggested category with
    confidence ≥ ``AI_CATEGORY_MIN_CONFIDENCE`` (0.7), use the AI's bucket
    + remediation. Otherwise fall back to ``infer_category`` (keyword
    heuristic). Both paths log a clear marker
    (``AI_SUGGESTION`` vs ``HEURISTIC_FALLBACK``) so we can monitor
    accuracy in prod.
    """
    if verdict_action not in ("FAIL", "REVIEW"):
        return None

    rejected_at = utcnow()
    customer_slug: str | None = None
    site_id: int | None = None
    supplier = call.detected_supplier

    try:  # best-effort customer/site lookup via deal
        from app.models import Customer, CustomerDeal

        if call.deal_id:
            deal = db.query(CustomerDeal).filter(CustomerDeal.id == call.deal_id).first()
            if deal is not None:
                site_id = deal.external_watt_site_id
                if deal.customer_id:
                    cust = (
                        db.query(Customer)
                        .filter(Customer.id == deal.customer_id)
                        .first()
                    )
                    if cust is not None:
                        customer_slug = cust.slug
                        if site_id is None:
                            site_id = cust.external_watt_site_id
    except Exception:
        # The auto-create must never fail the verdict submit; swallow and
        # log so we still get a rejection row even with sparse links.
        log.warning("REJECTION_AUTO_CREATE_link_lookup_failed", exc_info=True)

    # W4.7 — prefer AI suggestion over heuristic when confidence ≥ 0.7.
    ai_cat, ai_fix, ai_conf = _resolve_ai_suggestion(checkpoint)
    if (
        ai_cat is not None
        and ai_conf is not None
        and ai_conf >= AI_CATEGORY_MIN_CONFIDENCE
    ):
        category = ai_cat
        fix_required = ai_fix  # may be None — that's OK, fix_required is nullable
        decision_path = "AI_SUGGESTION"
    else:
        category = infer_category(reason, rule_id)
        fix_required = None
        decision_path = "HEURISTIC_FALLBACK"

    # Sprint A1 — pull Claude's own rejection-tracker narrative (one-line
    # headline + 2-4 sentence coaching notes) off either a JSON checkpoint
    # dict (call.checkpoint_results) or a CallCheckpoint ORM row. Falls
    # back to the manual reviewer reason when AI fields are missing so
    # pre-A1 calls + analyzer errors keep working.
    ai_rejection_reason: str | None = None
    ai_narrative_notes: str | None = None
    if isinstance(checkpoint, dict):
        ai_rejection_reason = checkpoint.get("ai_rejection_reason")
        ai_narrative_notes = checkpoint.get("ai_narrative_notes")
    elif checkpoint is not None:
        ai_rejection_reason = getattr(checkpoint, "ai_rejection_reason", None)
        ai_narrative_notes = getattr(checkpoint, "ai_narrative_notes", None)
    # Sanitize — empty strings count as missing.
    if isinstance(ai_rejection_reason, str) and not ai_rejection_reason.strip():
        ai_rejection_reason = None
    if isinstance(ai_narrative_notes, str) and not ai_narrative_notes.strip():
        ai_narrative_notes = None

    final_reason = (
        ai_rejection_reason
        or reason
        or "Auto-created from FAIL verdict"
    )

    rid = uuid.uuid4()
    # 2026-05-14 audit fix: AI-generated narrative belongs in `fix_narrative`
    # (the AI coaching slot), never `outcome_narrative` (the human reviewer
    # scratchpad). Previously the two were collapsed and a reviewer's
    # post-Confirm edit overwrote the AI text — silently destroying the
    # forensic trail. The tracker Notes column reads `outcome_narrative`,
    # so this also means freshly-created AI rejections no longer carry a
    # stale AI sentence into the reviewer's notes field.
    r = Rejection(
        id=rid,
        call_id=getattr(call, "id", None),
        customer_slug=customer_slug,
        external_watt_site_id=site_id,
        supplier=supplier,
        sales_agent=getattr(call, "agent_name", None),
        category=category,
        rejection_reason=final_reason[:1000],
        fix_narrative=ai_narrative_notes,
        outcome_narrative=None,
        fix_required=fix_required,
        status="NOT_STARTED",
        rejected_at=rejected_at,
        deadline=_compute_deadline(rejected_at),
        created_at=utcnow(),
        # 2026-05-15: stamp reviewer provenance ON CREATE. This entire
        # helper is invoked only from human-triggered routes (submit_verdict
        # / override / POST /api/rejections) — never the pipeline — so the
        # row is reviewer-confirmed by construction. Without these the
        # /rejections page's ``source=reviewer`` filter (which checks
        # ``confirmed_by IS NOT NULL``) silently hides the row even though
        # a human just created it.
        confirmed_by=actor_id,
        confirmed_at=utcnow(),
        # 2026-05-16 audit P1-1 — stamp HUMAN_CONFIRMED on creation. The
        # column has server_default="AI_PENDING", so without this every
        # auto-rejection created from a reviewer's FAIL verdict landed in
        # the DB tagged AI_PENDING — misclassifying it back into the
        # "awaiting review" bucket and confusing the tracker's verdict-state
        # filter. The helper is reviewer-only by construction (see above),
        # so HUMAN_CONFIRMED is correct for every code path that reaches here.
        verdict_state="HUMAN_CONFIRMED",
    )
    db.add(r)
    db.flush()
    db.add(
        RejectionAuditLog(
            id=uuid.uuid4(),
            rejection_id=rid,
            actor_id=actor_id,
            action="created",
            from_status=None,
            to_status="NOT_STARTED",
            notes="Auto-created from verdict",
            created_at=utcnow(),
        )
    )

    # Sprint C2 — back-link the rejection to its parent Deal and flip the
    # deal to ``closed_lost`` (unless it's already terminal). Best-effort:
    # any failure here is logged + swallowed so the rejection itself still
    # commits cleanly.
    try:
        from app.models import CustomerDeal as _Deal

        if getattr(call, "deal_id", None):
            deal = db.query(_Deal).filter(_Deal.id == call.deal_id).first()
            if deal is not None:
                deal.rejection_id = rid
                if deal.status not in ("closed_lost", "closed_done"):
                    deal.status = "closed_lost"
    except Exception:  # pragma: no cover — defensive
        log.warning("REJECTION_AUTO_CREATE_deal_link_failed", exc_info=True)

    log.info(
        f"REJECTION_AUTO_CREATED id={rid} call_id={getattr(call, 'id', None)} "
        f"category={r.category} fix={r.fix_required} path={decision_path} "
        f"ai_conf={ai_conf if ai_conf is not None else '-'} "
        f"ai_reason={'yes' if ai_rejection_reason else 'no'} "
        f"actor={actor_id}"
    )

    # Inngest observability — fire-and-forget so this never blocks the
    # API response. Surfaces every auto-created rejection in the
    # Inngest dashboard alongside upload + finalize events.
    try:
        from app.workflows.events import REJECTION_AUTO_CREATED, DEAL_STATUS_CHANGED
        from app.workflows.observability import emit_event
        emit_event(REJECTION_AUTO_CREATED, {
            "rejection_id": rid,
            "call_id": getattr(call, "id", None),
            "deal_id": str(getattr(call, "deal_id", "")) if getattr(call, "deal_id", None) else None,
            "category": r.category,
            "fix_required": r.fix_required,
            "decision_path": decision_path,
            "ai_confidence": ai_conf,
            "actor_id": actor_id,
        })
        # If we flipped the deal status above, surface that too.
        try:
            deal_for_event = db.query(_Deal).filter(_Deal.id == call.deal_id).first() if getattr(call, "deal_id", None) else None
            if deal_for_event is not None and deal_for_event.status == "closed_lost":
                emit_event(DEAL_STATUS_CHANGED, {
                    "deal_id": str(deal_for_event.id),
                    "from_status": "in_progress",
                    "to_status": "closed_lost",
                    "rejection_id": rid,
                    "actor_id": actor_id,
                })
        except Exception:
            pass
    except Exception:  # pragma: no cover — observability must not break business path
        pass

    return r
