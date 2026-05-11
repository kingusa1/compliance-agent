"""Tracker page row aggregator.

Mirrors Watt's `Compliance tracker example.xlsx` 17-col schema (A-Q).
Returns one row per Rejection (Active/Fixed/Dead tabs) or one row per
passing Call with no rejection (Compliant tab). Empty cols use ``None``
so the same dict shape works on both row types.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, TypedDict

from sqlalchemy import func, or_
from sqlalchemy import text as _sql_text
from sqlalchemy.orm import Session

from app.models import Call, Customer, CustomerDeal, Rejection


class TrackerRow(TypedDict):
    customer_name: Optional[str]            # col A
    mpan_mprn: Optional[str]                # col B
    expected_live_date: Optional[datetime]  # col C
    deal_value_gbp: Optional[float]         # col D
    supplier: Optional[str]                 # col E
    rejected_at: Optional[datetime]         # col F
    sales_agent: Optional[str]              # col G
    rejection_reason: Optional[str]         # col H
    category: Optional[str]                 # col I
    fix_required: Optional[str]             # col J
    fix_assignee_id: Optional[str]          # col K
    status: Optional[str]                   # col L
    last_action_date: Optional[datetime]    # col M
    deadline: Optional[datetime]            # col N
    outcome: Optional[str]                  # col O
    notes: Optional[str]                    # col P
    score: Optional[str]                    # extra (Compliant tab only)
    # Routing identifiers (not rendered as XLSX cols, but needed for clicks):
    call_id: Optional[str]
    rejection_id: Optional[str]
    deal_id: Optional[str]
    # Per-field provenance (B6): merged from CustomerDeal.field_sources +
    # Rejection.field_sources. Rejection sources win on key conflict — they
    # carry the more-specific provenance for fields present on both rows.
    # Values are one of: "human" | "xlsx_import" | "integration" | "ai" |
    # "placeholder". Frontend uses this to render AI/Human badges.
    field_sources: dict


_ACTIVE_STATUSES = ("NOT_STARTED", "IN_PROGRESS")
_FIXED_STATUSES = (
    "FIXED",
    "BATCHED_TO_PORTAL",
    "SUBMITTED_TO_PORTAL",
    "FIXED_AND_APPROVED",
)
_DEAD_STATUSES = ("DEAD",)


def _last_action_date(db: Session, rejection_id) -> Optional[datetime]:
    row = db.execute(
        _sql_text(
            "SELECT MAX(created_at) FROM rejection_audit_log "
            "WHERE rejection_id = :rid"
        ),
        {"rid": str(rejection_id)},
    ).first()
    return row[0] if row and row[0] is not None else None


def _rejection_row(
    rej: Rejection,
    deal: Optional[CustomerDeal],
    call: Optional[Call],
    db: Session,
) -> TrackerRow:
    cust_name = (deal.customer_name if deal else None) or (
        call.customer_name if call else None
    )
    return {
        "customer_name": cust_name,
        "mpan_mprn": deal.mpan_or_mprn if deal else None,
        "expected_live_date": deal.expected_live_date if deal else None,
        "deal_value_gbp": float(deal.deal_value_gbp)
        if deal and deal.deal_value_gbp is not None
        else None,
        "supplier": rej.supplier or (deal.supplier if deal else None),
        "rejected_at": rej.rejected_at,
        "sales_agent": rej.sales_agent or (call.agent_name if call else None),
        "rejection_reason": rej.rejection_reason,
        "category": rej.category,
        "fix_required": rej.fix_required,
        "fix_assignee_id": rej.fix_assignee_id,
        "status": rej.status,
        "last_action_date": _last_action_date(db, rej.id),
        "deadline": rej.deadline,
        "outcome": rej.outcome,
        "notes": rej.outcome_narrative,
        "fix_narrative": getattr(rej, "fix_narrative", None),
        "score": None,
        "call_id": rej.call_id,
        "rejection_id": str(rej.id),
        "deal_id": str(deal.id) if deal else None,
        # AI/HUMAN provenance gate (legacy rows tolerated via getattr).
        "verdict_state": getattr(rej, "verdict_state", None) or "AI_PENDING",
        "confirmed_by": getattr(rej, "confirmed_by", None),
        "confirmed_at": getattr(rej, "confirmed_at", None),
        # B6: merge deal + rejection provenance. Rejection wins on conflict.
        "field_sources": {
            **((deal.field_sources or {}) if deal else {}),
            **(rej.field_sources or {}),
        },
    }


def _compliant_row(call: Call, deal: Optional[CustomerDeal]) -> TrackerRow:
    cust_name = (deal.customer_name if deal else None) or call.customer_name
    return {
        "customer_name": cust_name,
        "mpan_mprn": deal.mpan_or_mprn if deal else None,
        "expected_live_date": deal.expected_live_date if deal else None,
        "deal_value_gbp": float(deal.deal_value_gbp)
        if deal and deal.deal_value_gbp is not None
        else None,
        "supplier": (deal.supplier if deal else None) or call.detected_supplier,
        "rejected_at": None,
        "sales_agent": call.agent_name,
        "rejection_reason": None,
        "category": None,
        "fix_required": None,
        "fix_assignee_id": None,
        "status": None,
        "last_action_date": None,
        "deadline": None,
        "outcome": None,
        "notes": None,
        "score": call.score,
        "call_id": call.id,
        "rejection_id": None,
        "deal_id": str(deal.id) if deal else None,
        # B6: compliant tab has no rejection — deal field_sources is all we have.
        "field_sources": dict((deal.field_sources or {}) if deal else {}),
    }


def build_tracker_rows(
    db: Session,
    *,
    tab: str = "active",
    month: Optional[str] = None,        # "YYYY-MM"
    category: Optional[list[str]] = None,
    supplier: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 500,
) -> list[TrackerRow]:
    """Return rows for the requested tab.

    ``tab`` ∈ active | fixed | dead | compliant | awaiting_review.
    awaiting_review = rejections with verdict_state=AI_PENDING (any status),
    surfaced as a reviewer queue separate from the active/fixed/dead workflow.
    """
    if tab == "awaiting_review":
        # Exclude orphan rejections (call_id IS NULL) — they survive the
        # ON DELETE SET NULL FK from rejections→calls and would otherwise
        # show up in the queue with empty customer/agent columns. The
        # audit row is preserved in the DB; we just don't surface it as
        # a reviewer action item. 2026-05-11.
        q = db.query(Rejection).filter(
            Rejection.verdict_state == "AI_PENDING",
            Rejection.call_id.isnot(None),
        )
        if category:
            q = q.filter(Rejection.category.in_(category))
        if supplier:
            q = q.filter(Rejection.supplier == supplier)
        if month:
            q = q.filter(func.to_char(Rejection.rejected_at, "YYYY-MM") == month)
        if search:
            like = f"%{search}%"
            q = q.filter(or_(
                Rejection.customer_slug.ilike(like),
                Rejection.sales_agent.ilike(like),
                Rejection.rejection_reason.ilike(like),
            ))
        rejections = q.order_by(Rejection.rejected_at.desc()).limit(limit).all()
        rows = []
        for rej in rejections:
            call = db.query(Call).filter(Call.id == rej.call_id).first() if rej.call_id else None
            deal = (
                db.query(CustomerDeal).filter(CustomerDeal.id == call.deal_id).first()
                if call and call.deal_id
                else None
            )
            rows.append(_rejection_row(rej, deal, call, db))
        return rows

    if tab == "compliant":
        # Calls that completed AND have no Rejection row.
        q = db.query(Call).filter(Call.status == "completed")
        sub = db.query(Rejection.call_id).filter(Rejection.call_id == Call.id).exists()
        q = q.filter(~sub)
        if supplier:
            q = q.filter(Call.detected_supplier == supplier)
        if search:
            like = f"%{search}%"
            q = q.filter(or_(
                Call.customer_name.ilike(like),
                Call.agent_name.ilike(like),
            ))
        calls = q.order_by(Call.created_at.desc()).limit(limit).all()
        rows: list[TrackerRow] = []
        for call in calls:
            deal = (
                db.query(CustomerDeal).filter(CustomerDeal.id == call.deal_id).first()
                if call.deal_id
                else None
            )
            rows.append(_compliant_row(call, deal))
        return rows

    # Rejection-row tabs:
    statuses = {
        "active": _ACTIVE_STATUSES,
        "fixed": _FIXED_STATUSES,
        "dead": _DEAD_STATUSES,
    }.get(tab, _ACTIVE_STATUSES)

    # Same orphan-rejection guard as awaiting_review — never surface
    # a rejection whose parent call was deleted.
    q = db.query(Rejection).filter(
        Rejection.status.in_(statuses),
        Rejection.call_id.isnot(None),
    )
    if category:
        q = q.filter(Rejection.category.in_(category))
    if supplier:
        q = q.filter(Rejection.supplier == supplier)
    if month:
        # YYYY-MM filter on rejected_at (Postgres to_char).
        q = q.filter(func.to_char(Rejection.rejected_at, "YYYY-MM") == month)
    if search:
        like = f"%{search}%"
        q = q.filter(or_(
            Rejection.customer_slug.ilike(like),
            Rejection.sales_agent.ilike(like),
            Rejection.rejection_reason.ilike(like),
        ))
    # Sort newest first by created_at (upload time) so freshly-processed
    # calls land at the top — reviewers spot new work immediately. Falls
    # back to rejected_at if created_at is null (XLSX-imported rows).
    rejections = q.order_by(
        Rejection.created_at.desc().nullslast(),
        Rejection.rejected_at.desc().nullslast(),
    ).limit(limit).all()
    rows = []
    for rej in rejections:
        call = db.query(Call).filter(Call.id == rej.call_id).first() if rej.call_id else None
        deal = (
            db.query(CustomerDeal).filter(CustomerDeal.id == call.deal_id).first()
            if call and call.deal_id else None
        )
        rows.append(_rejection_row(rej, deal, call, db))
    return rows
