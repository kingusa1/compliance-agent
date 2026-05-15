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

from app.models import Call, CallCheckpoint, Customer, CustomerDeal, Rejection


class TrackerRow(TypedDict, total=False):
    """Row shape returned by `/api/tracker/rows`.

    ``total=False`` because some keys (e.g. ``fix_narrative``, ``verdict_state``,
    ``confirmed_by``, ``confirmed_at``) only make sense on Rejection rows and
    are absent on Compliant/Awaiting-review rows.
    """

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
    # XLSX col P = "Notes". Backed by Rejection.outcome_narrative — same
    # column on disk; the alias only existed to mirror the XLSX header.
    # 2026-05-14: collapsed to a single canonical name (`outcome_narrative`)
    # so the side-panel Notes textarea reads what the aggregator emits
    # instead of always getting `undefined` via `row.outcome_narrative`.
    outcome_narrative: Optional[str]        # col P
    fix_narrative: Optional[str]            # rejection-only free-text
    score: Optional[str]                    # extra (Compliant tab only)
    # Routing identifiers (not rendered as XLSX cols, but needed for clicks):
    call_id: Optional[str]
    rejection_id: Optional[str]
    deal_id: Optional[str]
    # AI/HUMAN provenance gate — present on every row type now (was missing
    # from _compliant_row pre-2026-05-14 audit so the frontend silently got
    # `undefined` on the Compliant tab).
    verdict_state: Optional[str]
    confirmed_by: Optional[str]
    confirmed_at: Optional[datetime]
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
    """Most recent audit-log timestamp for a rejection.

    Wrapped in narrow try/except so a missing ``rejection_audit_log`` table
    (freshly-cloned env that hasn't run the audit-log migration yet) does
    NOT take down the whole tracker page. Surfaces as "—" in the Last-
    activity column.

    Narrow to ``OperationalError`` / ``ProgrammingError`` per the security
    review: a broad ``except Exception`` would also swallow OOM, KeyboardInterrupt-
    descended runtime issues, etc. — operational DB errors are the only
    family we want to silence. Anything else still propagates.
    """
    from sqlalchemy.exc import OperationalError, ProgrammingError

    from app.logger import log as _log

    try:
        row = db.execute(
            _sql_text(
                "SELECT MAX(created_at) FROM rejection_audit_log "
                "WHERE rejection_id = :rid"
            ),
            {"rid": str(rejection_id)},
        ).first()
        return row[0] if row and row[0] is not None else None
    except (OperationalError, ProgrammingError) as e:
        _log.warning(
            f"⚠️ tracker._last_action_date: rejection_audit_log query "
            f"failed (rid={rejection_id}); returning None: {type(e).__name__}: {e}"
        )
        return None


def _compose_mpan_mprn(deal: Optional[CustomerDeal]) -> Optional[str]:
    """Combined MPAN/MPRN display string for the tracker row.

    Prefers the new split columns ``mpan_electricity`` / ``mprn_gas`` so
    reviewer edits via the side-panel PATCH endpoint surface immediately
    (the legacy ``mpan_or_mprn`` is only filled by XLSX import). When
    both split columns are blank, falls back to the legacy column.
    """
    if not deal:
        return None
    parts: list[str] = []
    if deal.mpan_electricity:
        parts.append(str(deal.mpan_electricity))
    if deal.mprn_gas:
        parts.append(str(deal.mprn_gas))
    if parts:
        return " / ".join(parts)
    return deal.mpan_or_mprn


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
        "mpan_mprn": _compose_mpan_mprn(deal),
        "mpan_electricity": deal.mpan_electricity if deal else None,
        "mprn_gas": deal.mprn_gas if deal else None,
        "docusign_reference": deal.docusign_reference if deal else None,
        "term_months": deal.term_months if deal else None,
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
        "outcome_narrative": rej.outcome_narrative,
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


def _ai_suggestions_for_call(db: Session, call_id) -> dict:
    """Aggregate per-checkpoint AI suggestions into one row-level summary.

    2026-05-14 ask from reviewer: the tracker's awaiting-review tab was
    showing "—" for Category / Fix / Deadline because those fields only
    existed on rejection rows. The AI already emits a `suggested_category`
    + `suggested_fix_required` per failing checkpoint (stamped into
    CallCheckpoint at pipeline grade time) — we just weren't surfacing
    them on the call-level tracker row.

    Heuristic: most-common (modal) `ai_category` and `ai_fix_required`
    across all FAILED checkpoints. Plurality wins; ties broken by sum of
    confidences. Returns ``None`` for both when there are no failed
    checkpoints OR every failed checkpoint has empty ai_* fields.
    """
    rows = (
        db.query(CallCheckpoint)
        .filter(CallCheckpoint.call_id == str(call_id))
        .filter(CallCheckpoint.passed.is_(False))
        .all()
    )
    if not rows:
        return {"category": None, "fix_required": None, "ai_rejection_reason": None}

    cat_score: dict[str, float] = {}
    fix_score: dict[str, float] = {}
    reasons: list[str] = []
    for cp in rows:
        cat = (getattr(cp, "ai_category", None) or "").strip() or None
        fix = (getattr(cp, "ai_fix_required", None) or "").strip() or None
        conf = float(getattr(cp, "ai_category_confidence", None) or 0.5)
        if cat:
            cat_score[cat] = cat_score.get(cat, 0.0) + conf
        if fix:
            fix_score[fix] = fix_score.get(fix, 0.0) + conf
        rj = (getattr(cp, "ai_rejection_reason", None) or "").strip()
        if rj:
            reasons.append(rj)

    top_cat = max(cat_score, key=cat_score.get) if cat_score else None
    top_fix = max(fix_score, key=fix_score.get) if fix_score else None
    # Pick the most-confident rejection-reason if there's more than one.
    top_reason = reasons[0] if reasons else None
    return {
        "category": top_cat,
        "fix_required": top_fix,
        "ai_rejection_reason": top_reason,
    }


def _awaiting_review_row(
    call: Call,
    deal: Optional[CustomerDeal],
    db: Session,
) -> TrackerRow:
    """Tracker row for a Call that's awaiting reviewer sign-off.

    Mirrors `_compliant_row` shape but seeds rejection-shape columns from
    the AI verdict so the existing tracker UI can render them without a
    schema branch.

    2026-05-14 audit: Category / Fix Required / Deadline now flow from
    the per-checkpoint AI suggestions via `_ai_suggestions_for_call` so
    the reviewer has something to read without having to open the call
    detail page. The reviewer's own choices still override on Confirm.
    Deadline is computed as completed_at + 2 days (matches the rejected
    deadline rule in models.Rejection).

    ``status`` becomes a synthetic 'AWAITING_REVIEW' so the table knows
    this row isn't a real rejection — paired with the amber "Awaiting
    review" pill in StatusPipelinePill.
    """
    cust_name = (deal.customer_name if deal else None) or call.customer_name

    ai = _ai_suggestions_for_call(db, call.id)

    # Deadline = completed_at + 2 days. Mirrors Rejection.deadline so the
    # reviewer sees the same SLA pressure for awaiting-review rows.
    deadline: Optional[datetime] = None
    base = call.completed_at or call.created_at
    if base is not None:
        try:
            from datetime import timedelta
            deadline = base + timedelta(days=2)
        except Exception:
            deadline = None

    return {
        "customer_name": cust_name,
        "mpan_mprn": _compose_mpan_mprn(deal),
        "mpan_electricity": deal.mpan_electricity if deal else None,
        "mprn_gas": deal.mprn_gas if deal else None,
        "docusign_reference": deal.docusign_reference if deal else None,
        "term_months": deal.term_months if deal else None,
        "expected_live_date": deal.expected_live_date if deal else None,
        "deal_value_gbp": float(deal.deal_value_gbp)
        if deal and deal.deal_value_gbp is not None
        else None,
        "supplier": (deal.supplier if deal else None) or call.detected_supplier,
        "rejected_at": call.created_at,
        "sales_agent": call.agent_name,
        # Prefer the synthesized AI rejection reason (per-checkpoint) over
        # the call-level reason summary — it's more actionable.
        "rejection_reason": ai.get("ai_rejection_reason") or call.reason,
        "category": ai.get("category"),
        "fix_required": ai.get("fix_required"),
        "fix_assignee_id": None,
        "status": "AWAITING_REVIEW",
        "last_action_date": call.completed_at,
        "deadline": deadline,
        "outcome": None,
        "outcome_narrative": None,
        "fix_narrative": None,
        "score": call.score,
        "call_id": call.id,
        "rejection_id": None,
        "deal_id": str(deal.id) if deal else None,
        "verdict_state": "AI_PENDING",
        "confirmed_by": None,
        "confirmed_at": None,
        "field_sources": dict((deal.field_sources or {}) if deal else {}),
    }


def _compliant_row(call: Call, deal: Optional[CustomerDeal]) -> TrackerRow:
    cust_name = (deal.customer_name if deal else None) or call.customer_name
    return {
        "customer_name": cust_name,
        "mpan_mprn": _compose_mpan_mprn(deal),
        "mpan_electricity": deal.mpan_electricity if deal else None,
        "mprn_gas": deal.mprn_gas if deal else None,
        "docusign_reference": deal.docusign_reference if deal else None,
        "term_months": deal.term_months if deal else None,
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
        "outcome_narrative": None,
        "fix_narrative": None,
        "score": call.score,
        "call_id": call.id,
        "rejection_id": None,
        "deal_id": str(deal.id) if deal else None,
        # 2026-05-14 audit fix: emit the verdict-state triple on EVERY row
        # type so the frontend never gets `undefined` for these keys.
        # Compliant rows have NEVER been touched by a human, so emit
        # AI_PENDING — matching the new-arrival default everywhere else in
        # the system. Earlier same-session attempt to use HUMAN_CONFIRMED
        # was reverted because the hitl_routes filter at line ~1189 would
        # have mis-included compliant rows as human-reviewed.
        "verdict_state": "AI_PENDING",
        "confirmed_by": None,
        "confirmed_at": None,
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
    # 2026-05-15 advanced filters — every field optional, falsy=ignore.
    suppliers: Optional[list[str]] = None,         # multi-select supplier
    agents: Optional[list[str]] = None,            # multi-select sales_agent
    statuses: Optional[list[str]] = None,          # multi-select rejection status
    verdict_states: Optional[list[str]] = None,    # AI_PENDING|HUMAN_CONFIRMED|HUMAN_OVERRIDDEN
    date_from: Optional[str] = None,               # ISO yyyy-mm-dd inclusive
    date_to: Optional[str] = None,                 # ISO yyyy-mm-dd inclusive
    date_on: Optional[str] = None,                 # ISO yyyy-mm-dd single day
    meter: Optional[str] = None,                   # substring MPAN/MPRN match
    value_min: Optional[float] = None,
    value_max: Optional[float] = None,
    deadline_state: Optional[str] = None,          # overdue|due_3d|due_7d|on_track
) -> list[TrackerRow]:
    """Return rows for the requested tab.

    ``tab`` ∈ active | fixed | dead | compliant | awaiting_review.
    awaiting_review = rejections with verdict_state=AI_PENDING (any status),
    surfaced as a reviewer queue separate from the active/fixed/dead workflow.

    Filter handling: legacy single-value params (``supplier``, ``month``)
    are honoured for backward-compat; the new multi-value lists win when
    both are provided.
    """
    # ---- helpers for the advanced-filter block ----
    from datetime import date, datetime, timedelta

    def _parse_iso_date(s: Optional[str]):
        if not s:
            return None
        try:
            return datetime.strptime(s.strip(), "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None

    df = _parse_iso_date(date_from)
    dt = _parse_iso_date(date_to)
    don = _parse_iso_date(date_on)
    if don:
        # date_on overrides range — collapses to a single day.
        df = don
        dt = don

    today = date.today()
    deadline_cutoff: Optional[date] = None
    if deadline_state == "due_3d":
        deadline_cutoff = today + timedelta(days=3)
    elif deadline_state == "due_7d":
        deadline_cutoff = today + timedelta(days=7)

    # When any deal-level filter is active, narrow to a sub-population of
    # deal_ids first; later branches use ``Call.deal_id.in_(...)`` /
    # ``Rejection.call_id.in_(call_ids_for_deals)`` so we don't have to
    # JOIN CustomerDeal into every branch.
    restricted_deal_ids: Optional[list] = None
    if meter or value_min is not None or value_max is not None:
        dq = db.query(CustomerDeal.id)
        if meter:
            mlike = f"%{meter}%"
            dq = dq.filter(or_(
                CustomerDeal.mpan_electricity.ilike(mlike),
                CustomerDeal.mprn_gas.ilike(mlike),
            ))
        if value_min is not None:
            dq = dq.filter(CustomerDeal.deal_value_gbp >= value_min)
        if value_max is not None:
            dq = dq.filter(CustomerDeal.deal_value_gbp <= value_max)
        restricted_deal_ids = [row[0] for row in dq.all()]
        if not restricted_deal_ids:
            # Empty deal set → empty result. Short-circuit so the branch
            # queries below don't generate a malformed ``IN ()`` clause.
            return []

    def _apply_call_advanced(q):
        """Apply df/dt/suppliers/agents/meter-via-deals to a Call query."""
        if df:
            q = q.filter(func.date(Call.created_at) >= df)
        if dt:
            q = q.filter(func.date(Call.created_at) <= dt)
        if suppliers:
            q = q.filter(Call.detected_supplier.in_(suppliers))
        if agents:
            q = q.filter(Call.agent_name.in_(agents))
        if restricted_deal_ids is not None:
            q = q.filter(Call.deal_id.in_(restricted_deal_ids))
        return q

    def _apply_rejection_advanced(q):
        """Apply df/dt/suppliers/agents/verdict_states/statuses/deadline_state/
        meter-via-deals to a Rejection query."""
        if df:
            q = q.filter(func.date(Rejection.rejected_at) >= df)
        if dt:
            q = q.filter(func.date(Rejection.rejected_at) <= dt)
        if suppliers:
            q = q.filter(Rejection.supplier.in_(suppliers))
        if agents:
            q = q.filter(Rejection.sales_agent.in_(agents))
        if verdict_states:
            q = q.filter(Rejection.verdict_state.in_(verdict_states))
        if statuses:
            # Multi-select overrides the tab→statuses default below.
            q = q.filter(Rejection.status.in_(statuses))
        if deadline_state == "overdue":
            q = q.filter(Rejection.deadline.isnot(None)).filter(
                func.date(Rejection.deadline) < today
            )
        elif deadline_state in ("due_3d", "due_7d") and deadline_cutoff is not None:
            q = q.filter(Rejection.deadline.isnot(None)).filter(
                func.date(Rejection.deadline) >= today,
                func.date(Rejection.deadline) <= deadline_cutoff,
            )
        elif deadline_state == "on_track":
            q = q.filter(or_(
                Rejection.deadline.is_(None),
                func.date(Rejection.deadline) > today,
            ))
        if restricted_deal_ids is not None:
            # Filter to rejections whose call's deal is in the restricted set.
            call_ids = [
                row[0]
                for row in db.query(Call.id).filter(
                    Call.deal_id.in_(restricted_deal_ids)
                ).all()
            ]
            if not call_ids:
                # Force empty result through a sentinel filter.
                q = q.filter(Rejection.id == None)  # noqa: E711
            else:
                q = q.filter(Rejection.call_id.in_(call_ids))
        return q
    if tab == "awaiting_review":
        # Post-2026-05-12: rejections are reviewer-initiated only, so the
        # awaiting_review tab can no longer be sourced from Rejection rows.
        # Instead we surface every completed Call that the reviewer has
        # NOT yet signed off (review_status != 'reviewed'), regardless of
        # AI verdict — both Non-Compliant flagged and Compliant calls need
        # a human pass before they leave this queue.
        q = db.query(Call).filter(
            Call.status == "completed",
            or_(Call.review_status.is_(None), Call.review_status != "reviewed"),
        )
        # 2026-05-14 audit: a call that already has a reviewer-initiated
        # Rejection has moved into the active/fixed/dead workflow and must
        # disappear from the awaiting-review queue. Otherwise the reviewer
        # sees the same call in TWO tabs simultaneously and gets confused
        # about whether it still needs attention.
        sub = db.query(Rejection.call_id).filter(Rejection.call_id == Call.id).exists()
        q = q.filter(~sub)
        if supplier:
            q = q.filter(Call.detected_supplier == supplier)
        if month:
            q = q.filter(func.to_char(Call.created_at, "YYYY-MM") == month)
        if search:
            like = f"%{search}%"
            q = q.filter(or_(
                Call.customer_name.ilike(like),
                Call.agent_name.ilike(like),
            ))
        q = _apply_call_advanced(q)
        # Newest first so freshly-processed calls land at the top.
        calls = q.order_by(Call.created_at.desc()).limit(limit).all()
        # 2026-05-15 N+1 fix: bulk-load all CustomerDeals referenced by the
        # in-page calls in ONE query. Was: 1 + N per-row .first() calls,
        # giving 101 round-trips for a 100-row page on Supabase pooler
        # (~10ms RTT each → 1s+ before any compute). Now: 2 round-trips
        # total regardless of page size.
        deal_ids = {c.deal_id for c in calls if c.deal_id}
        deals_by_id: dict = {}
        if deal_ids:
            for d in db.query(CustomerDeal).filter(CustomerDeal.id.in_(deal_ids)).all():
                deals_by_id[d.id] = d
        rows: list[TrackerRow] = []
        for call in calls:
            deal = deals_by_id.get(call.deal_id) if call.deal_id else None
            rows.append(_awaiting_review_row(call, deal, db))
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
        q = _apply_call_advanced(q)
        calls = q.order_by(Call.created_at.desc()).limit(limit).all()
        # 2026-05-15 N+1 fix — see awaiting_review branch above for rationale.
        deal_ids = {c.deal_id for c in calls if c.deal_id}
        deals_by_id: dict = {}
        if deal_ids:
            for d in db.query(CustomerDeal).filter(CustomerDeal.id.in_(deal_ids)).all():
                deals_by_id[d.id] = d
        rows: list[TrackerRow] = []
        for call in calls:
            deal = deals_by_id.get(call.deal_id) if call.deal_id else None
            rows.append(_compliant_row(call, deal))
        return rows

    # Rejection-row tabs:
    # When the new ``statuses`` multi-select param is provided, it wins
    # outright (the user is explicitly asking for the union of states);
    # otherwise we fall back to the tab→default-status mapping.
    if not statuses:
        tab_statuses = {
            "active": _ACTIVE_STATUSES,
            "fixed": _FIXED_STATUSES,
            "dead": _DEAD_STATUSES,
        }.get(tab, _ACTIVE_STATUSES)
    else:
        tab_statuses = None  # _apply_rejection_advanced handles statuses

    # Same orphan-rejection guard as awaiting_review — never surface
    # a rejection whose parent call was deleted.
    q = db.query(Rejection).filter(Rejection.call_id.isnot(None))
    if tab_statuses is not None:
        q = q.filter(Rejection.status.in_(tab_statuses))
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
    q = _apply_rejection_advanced(q)
    # Sort newest first by created_at (upload time) so freshly-processed
    # calls land at the top — reviewers spot new work immediately. Falls
    # back to rejected_at if created_at is null (XLSX-imported rows).
    rejections = q.order_by(
        Rejection.created_at.desc().nullslast(),
        Rejection.rejected_at.desc().nullslast(),
    ).limit(limit).all()
    # 2026-05-15 N+1 fix: bulk-load all Calls + their CustomerDeals via two
    # IN(...) queries instead of per-rejection .first() calls. Removes the
    # 2N round-trip cost on the rejection-row tabs (active / fixed / dead).
    call_ids = {r.call_id for r in rejections if r.call_id}
    calls_by_id: dict = {}
    if call_ids:
        for c in db.query(Call).filter(Call.id.in_(call_ids)).all():
            calls_by_id[c.id] = c
    deal_ids = {c.deal_id for c in calls_by_id.values() if c.deal_id}
    deals_by_id: dict = {}
    if deal_ids:
        for d in db.query(CustomerDeal).filter(CustomerDeal.id.in_(deal_ids)).all():
            deals_by_id[d.id] = d
    rows = []
    for rej in rejections:
        call = calls_by_id.get(rej.call_id) if rej.call_id else None
        deal = deals_by_id.get(call.deal_id) if (call and call.deal_id) else None
        rows.append(_rejection_row(rej, deal, call, db))
    return rows
