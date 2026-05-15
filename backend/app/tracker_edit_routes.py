"""PATCH /api/tracker/rows/{id} — inline edit any whitelisted field on a
tracker row.

Two field families:

* **Rejection fields** (``REJECTION_FIELDS``) — written to ``Rejection``.
  Each successful edit flips ``Rejection.field_sources[<field>]='human'``,
  writes a ``ReviewerEdit`` audit row, and returns the fresh field_sources
  so the frontend can drop the AI badge.

* **Deal fields** (``DEAL_FIELDS``) — written to the parent ``CustomerDeal``
  via ``Rejection.call_id → Call.deal_id``. Deal-level edits also stamp
  ``deal.field_sources[<field>]='reviewer_edit'`` so /customers + /deals
  reflect the reviewer's override.

Plus side-channel endpoints:

* ``POST /api/tracker/rows/{rejection_id}/assignee`` — set fix_assignee_id
  with FK validation against ``profiles``.
* ``GET /api/reviewers/active`` — list active reviewer profiles for the
  side-panel assignee dropdown.

The whitelists are explicit so FK columns (call_id, deal_id, customer_slug)
and timestamps (created_at, rejected_at) can never be overwritten by an
arbitrary client payload.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.field_sources import set_source
from app.models import Call, CustomerDeal, Profile, Rejection, ReviewerEdit
from app.reviewers import current_reviewer


tracker_edit_router = APIRouter()


# Rejection-level whitelisted fields. Existing 9 plus deadline (2026-05-15).
REJECTION_FIELDS = {
    "supplier",
    "sales_agent",
    "category",
    "rejection_reason",
    "fix_required",
    "fix_narrative",
    "status",
    "outcome",
    "outcome_narrative",
    "deadline",  # 2026-05-15 — reviewer-editable due date
}

# Deal-level whitelisted fields. All optional; commission_value pairs with
# commission_unit ("pct"|"gbp") but each is independently editable.
DEAL_FIELDS = {
    "mpan_electricity",
    "mprn_gas",
    "deal_value_gbp",
    "expected_live_date",
    "term_months",
    "commission_value",
    "commission_unit",
    "docusign_reference",
}

# Back-compat re-export so legacy imports keep working.
ALLOWED_FIELDS = REJECTION_FIELDS | DEAL_FIELDS


def _coerce_value(field: str, raw: Any) -> Any:
    """Normalise client-supplied scalars to the column's Python type.

    Date-like strings come from <input type="date"> as 'YYYY-MM-DD';
    numeric strings come from <input type="number"> as 'NN.NN'. We coerce
    to date/Decimal/int defensively so SQLAlchemy gets the right type and
    the field_sources audit reflects the canonical form.
    """
    if raw in (None, ""):
        return None
    if field in ("deadline", "expected_live_date"):
        if isinstance(raw, (date, datetime)):
            return raw if isinstance(raw, date) else raw.date()
        try:
            return datetime.strptime(str(raw).strip(), "%Y-%m-%d").date()
        except ValueError as e:
            raise HTTPException(400, f"invalid {field}: expected YYYY-MM-DD ({e})")
    if field in ("deal_value_gbp", "commission_value"):
        try:
            return Decimal(str(raw))
        except (InvalidOperation, ValueError) as e:
            raise HTTPException(400, f"invalid {field}: expected number ({e})")
    if field == "term_months":
        try:
            v = int(raw)
        except (TypeError, ValueError):
            raise HTTPException(400, f"invalid {field}: expected integer")
        if v not in (12, 24, 36, 48, 60):
            raise HTTPException(400, f"invalid {field}: must be 12/24/36/48/60")
        return v
    if field == "commission_unit":
        if raw not in ("pct", "gbp"):
            raise HTTPException(400, "commission_unit must be 'pct' or 'gbp'")
        return raw
    if field in ("mpan_electricity", "mprn_gas"):
        # Strip non-digits — reviewer often pastes with spaces/hyphens.
        digits = "".join(ch for ch in str(raw) if ch.isdigit())
        return digits or None
    return raw


@tracker_edit_router.patch("/api/tracker/rows/{rejection_id}")
def patch_tracker_row(
    rejection_id: UUID,
    body: dict = Body(...),
    db: Session = Depends(get_db),
    user=Depends(current_reviewer),
):
    rej = db.query(Rejection).filter(Rejection.id == rejection_id).first()
    if not rej:
        raise HTTPException(404, "rejection not found")

    bad = [k for k in body.keys() if k not in ALLOWED_FIELDS]
    if bad:
        raise HTTPException(400, f"fields not editable: {bad}")

    # Resolve the linked deal once if any deal-level fields are present.
    deal: CustomerDeal | None = None
    has_deal_fields = any(k in DEAL_FIELDS for k in body)
    if has_deal_fields:
        call = (
            db.query(Call).filter(Call.id == rej.call_id).first()
            if rej.call_id
            else None
        )
        if call and call.deal_id:
            deal = (
                db.query(CustomerDeal).filter(CustomerDeal.id == call.deal_id).first()
            )
        if deal is None:
            raise HTTPException(
                400,
                "deal-level edits unavailable — rejection has no linked deal",
            )

    reviewer_id = str(user.get("id")) if isinstance(user, dict) else None

    for k, raw_v in body.items():
        v = _coerce_value(k, raw_v)
        if k in REJECTION_FIELDS:
            old = getattr(rej, k, None)
            if old != v:
                db.add(ReviewerEdit(
                    rejection_id=str(rej.id),
                    field=k,
                    old_value=str(old) if old is not None else None,
                    new_value=str(v) if v is not None else None,
                    reviewer_id=reviewer_id,
                ))
                setattr(rej, k, v)
                set_source(rej, k, "human")
        elif k in DEAL_FIELDS and deal is not None:
            old = getattr(deal, k, None)
            if old != v:
                db.add(ReviewerEdit(
                    rejection_id=str(rej.id),
                    field=f"deal.{k}",
                    old_value=str(old) if old is not None else None,
                    new_value=str(v) if v is not None else None,
                    reviewer_id=reviewer_id,
                ))
                setattr(deal, k, v)
                # Deal-level provenance map uses "reviewer_edit" to match
                # the existing /customers field-source legend.
                if hasattr(deal, "field_sources"):
                    fs = dict(deal.field_sources or {})
                    fs[k] = "reviewer_edit"
                    deal.field_sources = fs
    db.commit()
    db.refresh(rej)
    return {
        "id": str(rej.id),
        "field_sources": rej.field_sources,
        "deal_field_sources": dict(deal.field_sources or {}) if deal else None,
    }


@tracker_edit_router.post("/api/tracker/rows/{rejection_id}/assignee")
def set_tracker_assignee(
    rejection_id: UUID,
    body: dict = Body(...),
    db: Session = Depends(get_db),
    user=Depends(current_reviewer),
):
    """Assign a reviewer to a tracker row. ``assignee_id`` may be a UUID
    string of an active Profile or null to unassign."""
    rej = db.query(Rejection).filter(Rejection.id == rejection_id).first()
    if not rej:
        raise HTTPException(404, "rejection not found")
    raw = body.get("assignee_id")
    new_id: str | None
    if raw in (None, ""):
        new_id = None
    else:
        # Validate FK — reject unknown profile ids defensively.
        prof = db.query(Profile).filter(Profile.id == str(raw)).first()
        if prof is None:
            raise HTTPException(400, f"profile {raw!r} not found")
        new_id = prof.id

    old = rej.fix_assignee_id
    if old != new_id:
        reviewer_id = str(user.get("id")) if isinstance(user, dict) else None
        db.add(ReviewerEdit(
            rejection_id=str(rej.id),
            field="fix_assignee_id",
            old_value=old,
            new_value=new_id,
            reviewer_id=reviewer_id,
        ))
        rej.fix_assignee_id = new_id
        set_source(rej, "fix_assignee_id", "human")
        db.commit()
        db.refresh(rej)
    return {"id": str(rej.id), "fix_assignee_id": rej.fix_assignee_id}


@tracker_edit_router.get("/api/reviewers/active")
def list_active_reviewers(
    db: Session = Depends(get_db),
    user=Depends(current_reviewer),
):
    """Return active reviewer / lead / admin profiles for the assignee
    dropdown. Bare auth users without a Profile row are skipped — the
    dropdown should never list them.
    """
    rows = (
        db.query(Profile)
        .filter(Profile.active.is_(True))
        .filter(Profile.role.in_(("reviewer", "lead", "admin")))
        .order_by(Profile.name.asc(), Profile.email.asc())
        .all()
    )
    return [
        {
            "id": p.id,
            "email": p.email,
            "name": p.name,
            "role": p.role,
        }
        for p in rows
    ]


# ---------------------------------------------------------------------------
# Call-level meta PATCH — used by the tracker side panel on awaiting-review
# rows (which have no Rejection yet) and any other surface that needs to
# correct identity / meter / deal fields on a Call before it's rejected.
# ---------------------------------------------------------------------------


# Call-level whitelisted columns (live on the Call row itself).
CALL_META_FIELDS = {
    "customer_name",
    "agent_name",
    "detected_supplier",
}


@tracker_edit_router.patch("/api/calls/{call_id}/meta")
def patch_call_meta(
    call_id: str,
    body: dict = Body(...),
    db: Session = Depends(get_db),
    user=Depends(current_reviewer),
):
    """Inline-edit Call-level identity (agent / customer / supplier) and the
    linked deal's meter / value / term / DocuSign fields. Mirrors the
    rejection PATCH shape so the frontend can reuse the same payload keys.

    Routing:
        * ``customer_name`` / ``agent_name`` / ``detected_supplier`` →
          ``Call`` row.
        * Any DEAL_FIELDS key → the linked ``CustomerDeal`` (404 if the
          call has no deal).
        * ``supplier`` (alias) → ``Call.detected_supplier`` AND
          ``deal.supplier`` so the row reflects on every surface.

    Reviewer audit: each accepted edit appends a ``ReviewerEdit`` row with
    ``rejection_id=None`` and ``field`` prefixed by ``call.`` / ``deal.``
    so the audit log makes provenance obvious.
    """
    call = db.query(Call).filter(Call.id == call_id).first()
    if call is None:
        raise HTTPException(404, "call not found")

    # Defensively reject obvious garbage shapes.
    if not isinstance(body, dict):
        raise HTTPException(400, "body must be a JSON object")

    # The side panel uses the tracker display key ``sales_agent`` for the
    # Agent input on awaiting-review rows even though the underlying Call
    # column is ``agent_name``. Translate so the reviewer doesn't have to
    # know the schema difference.
    if "sales_agent" in body and "agent_name" not in body:
        body["agent_name"] = body.pop("sales_agent")

    accepted: dict[str, str] = {}  # field → "call" | "deal" | "both"
    for k in body:
        if k in CALL_META_FIELDS:
            accepted[k] = "call"
        elif k in DEAL_FIELDS:
            accepted[k] = "deal"
        elif k == "supplier":
            accepted[k] = "both"
        else:
            raise HTTPException(400, f"field not editable: {k!r}")

    deal: CustomerDeal | None = None
    if any(loc in ("deal", "both") for loc in accepted.values()):
        if not call.deal_id:
            raise HTTPException(
                400, "deal-level edits unavailable — call has no linked deal"
            )
        deal = (
            db.query(CustomerDeal).filter(CustomerDeal.id == call.deal_id).first()
        )
        if deal is None:
            raise HTTPException(
                400, "deal-level edits unavailable — linked deal missing"
            )

    reviewer_id = str(user.get("id")) if isinstance(user, dict) else None

    for k, raw_v in body.items():
        loc = accepted[k]
        if k == "supplier":
            # Dual write: Call.detected_supplier (keeps row.supplier display
            # honest) + Deal.supplier (canonical truth for /customers).
            v = raw_v or None
            old_call = call.detected_supplier
            if old_call != v:
                db.add(ReviewerEdit(
                    rejection_id=None,
                    field="call.detected_supplier",
                    old_value=old_call,
                    new_value=v,
                    reviewer_id=reviewer_id,
                ))
                call.detected_supplier = v
            if deal is not None:
                old_deal = deal.supplier
                if old_deal != v:
                    db.add(ReviewerEdit(
                        rejection_id=None,
                        field="deal.supplier",
                        old_value=old_deal,
                        new_value=v,
                        reviewer_id=reviewer_id,
                    ))
                    deal.supplier = v
                    if hasattr(deal, "field_sources"):
                        fs = dict(deal.field_sources or {})
                        fs["supplier"] = "reviewer_edit"
                        deal.field_sources = fs
            continue

        if loc == "call":
            v = raw_v or None
            old = getattr(call, k, None)
            if old != v:
                db.add(ReviewerEdit(
                    rejection_id=None,
                    field=f"call.{k}",
                    old_value=str(old) if old is not None else None,
                    new_value=str(v) if v is not None else None,
                    reviewer_id=reviewer_id,
                ))
                setattr(call, k, v)
            continue

        # loc == "deal"
        v = _coerce_value(k, raw_v)
        old = getattr(deal, k, None) if deal is not None else None
        if deal is not None and old != v:
            db.add(ReviewerEdit(
                rejection_id=None,
                field=f"deal.{k}",
                old_value=str(old) if old is not None else None,
                new_value=str(v) if v is not None else None,
                reviewer_id=reviewer_id,
            ))
            setattr(deal, k, v)
            if hasattr(deal, "field_sources"):
                fs = dict(deal.field_sources or {})
                fs[k] = "reviewer_edit"
                deal.field_sources = fs

    db.commit()
    db.refresh(call)
    return {
        "call_id": str(call.id),
        "deal_id": str(deal.id) if deal else None,
        "deal_field_sources": dict(deal.field_sources or {}) if deal else None,
        "applied_keys": list(accepted.keys()),
    }
