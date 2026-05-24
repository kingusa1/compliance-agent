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

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.database import get_db
from app.field_sources import set_source
from app.models import Call, CustomerDeal, Profile, Rejection, ReviewerEdit
from app.reviewers import current_reviewer


tracker_edit_router = APIRouter()


def _record_reviewer_edit(
    db: Session,
    *,
    rejection_id: str | None,
    call_id: str | None,
    field: str,
    old_value: Any,
    new_value: Any,
    reviewer_id: str | None,
) -> None:
    """Append a ReviewerEdit row, deduping React StrictMode double-invokes
    and short-window network retries.

    2026-05-24 wiring audit C10 — reviewer_edits has no DB unique
    constraint (composite would require a complex partial index across
    nullable columns). App-level guard skips an identical write within
    the last 2 seconds by the same reviewer on the same (target, field,
    old, new) tuple. Anything older is treated as a genuine re-edit.
    """
    cutoff = datetime.utcnow() - timedelta(seconds=2)
    new_str = str(new_value) if new_value is not None else None
    old_str = str(old_value) if old_value is not None else None
    q = db.query(ReviewerEdit).filter(
        ReviewerEdit.field == field,
        ReviewerEdit.reviewer_id == reviewer_id,
        ReviewerEdit.at >= cutoff,
        ReviewerEdit.new_value == new_str,
        ReviewerEdit.old_value == old_str,
    )
    if rejection_id is not None:
        q = q.filter(ReviewerEdit.rejection_id == rejection_id)
    else:
        q = q.filter(ReviewerEdit.rejection_id.is_(None))
    if call_id is not None:
        q = q.filter(ReviewerEdit.call_id == call_id)
    else:
        q = q.filter(ReviewerEdit.call_id.is_(None))
    if q.first() is not None:
        return  # dedupe — identical edit already pending in this 2s window
    db.add(ReviewerEdit(
        rejection_id=rejection_id,
        call_id=call_id,
        field=field,
        old_value=old_str,
        new_value=new_str,
        reviewer_id=reviewer_id,
    ))


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

    # 2026-05-24 audit — `supplier` and `sales_agent` are on the
    # REJECTION_FIELDS whitelist, but the aggregator and every other
    # surface (/deals, /customers, /deals/[id]) reads them from the
    # linked CustomerDeal / Call rows. Without a dual-write the rejection
    # PATCH stored the reviewer's correction on the rejection row only;
    # every other page kept showing the stale supplier/agent forever.
    # Map for the dual-write target column on each side.
    _REJECTION_TO_CALL = {"sales_agent": "agent_name"}
    _REJECTION_TO_DEAL = {"supplier": "supplier"}

    # Resolve the linked Call once per request so we don't refetch per
    # field. `rej.call_id` is the FK; missing on legacy rows is tolerated.
    call_row = None
    if rej.call_id:
        from app.models import Call
        call_row = db.query(Call).filter(Call.id == rej.call_id).first()

    for k, raw_v in body.items():
        v = _coerce_value(k, raw_v)
        if k in REJECTION_FIELDS:
            old = getattr(rej, k, None)
            if old != v:
                _record_reviewer_edit(
                    db,
                    rejection_id=str(rej.id),
                    call_id=None,
                    field=k,
                    old_value=old,
                    new_value=v,
                    reviewer_id=reviewer_id,
                )
                setattr(rej, k, v)
                set_source(rej, k, "human")

            # Dual-write to the linked Deal + Call for the two cross-row
            # fields. Reviewer intent on "supplier=British Gas" is "this
            # IS the supplier" — not "this is the supplier the auditor
            # cited" — so every surface needs to reflect it.
            deal_col = _REJECTION_TO_DEAL.get(k)
            if deal_col and deal is not None:
                old_deal = getattr(deal, deal_col, None)
                if old_deal != v:
                    _record_reviewer_edit(
                        db,
                        rejection_id=str(rej.id),
                        call_id=None,
                        field=f"deal.{deal_col}",
                        old_value=old_deal,
                        new_value=v,
                        reviewer_id=reviewer_id,
                    )
                    setattr(deal, deal_col, v)
                    if hasattr(deal, "field_sources"):
                        fs = dict(deal.field_sources or {})
                        fs[deal_col] = "reviewer_edit"
                        deal.field_sources = fs

            call_col = _REJECTION_TO_CALL.get(k)
            if call_col and call_row is not None:
                old_call = getattr(call_row, call_col, None)
                if old_call != v:
                    _record_reviewer_edit(
                        db,
                        rejection_id=str(rej.id),
                        call_id=str(call_row.id),
                        field=f"call.{call_col}",
                        old_value=old_call,
                        new_value=v,
                        reviewer_id=reviewer_id,
                    )
                    setattr(call_row, call_col, v)
            # supplier also propagates to Call.detected_supplier so the
            # tracker aggregator's `rej.supplier or call.detected_supplier`
            # display stays consistent on every refetch.
            if k == "supplier" and call_row is not None:
                old_det = getattr(call_row, "detected_supplier", None)
                if old_det != v:
                    _record_reviewer_edit(
                        db,
                        rejection_id=str(rej.id),
                        call_id=str(call_row.id),
                        field="call.detected_supplier",
                        old_value=old_det,
                        new_value=v,
                        reviewer_id=reviewer_id,
                    )
                    call_row.detected_supplier = v
        elif k in DEAL_FIELDS and deal is not None:
            old = getattr(deal, k, None)
            if old != v:
                _record_reviewer_edit(
                    db,
                    rejection_id=str(rej.id),
                    call_id=None,
                    field=f"deal.{k}",
                    old_value=old,
                    new_value=v,
                    reviewer_id=reviewer_id,
                )
                setattr(deal, k, v)
                # Deal-level provenance map uses "reviewer_edit" to match
                # the existing /customers field-source legend.
                if hasattr(deal, "field_sources"):
                    fs = dict(deal.field_sources or {})
                    fs[k] = "reviewer_edit"
                    deal.field_sources = fs
    try:
        db.commit()
    except (IntegrityError, OperationalError) as exc:
        db.rollback()
        # 2026-05-24 wiring audit HIGH-tracker-edit — surface DB integrity
        # failures (CHECK violations, FK races) as 409 with a safe message
        # rather than leaking stack traces with column values in a 500.
        raise HTTPException(409, f"edit could not be saved: {exc.__class__.__name__}")
    db.refresh(rej)
    # Realtime fan-out so other reviewers' tabs see the edit without poll.
    try:
        from app import realtime
        realtime.publish(
            rej.call_id or "",
            "rejection_updated",
            {"rejection_id": str(rej.id), "fields": list(body.keys())},
        )
    except Exception:
        pass  # best-effort; never block the response on realtime
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
        _record_reviewer_edit(
            db,
            rejection_id=str(rej.id),
            call_id=None,
            field="fix_assignee_id",
            old_value=old,
            new_value=new_id,
            reviewer_id=reviewer_id,
        )
        rej.fix_assignee_id = new_id
        set_source(rej, "fix_assignee_id", "human")
        try:
            db.commit()
        except (IntegrityError, OperationalError) as exc:
            db.rollback()
            raise HTTPException(409, f"assignee update failed: {exc.__class__.__name__}")
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


@tracker_edit_router.patch("/api/tracker/calls/{call_id}/meta")
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

    # 2026-05-24 — load the deal whenever a `customer_name` edit is in
    # the body too. The dual-write at the `if loc == "call"` branch
    # propagates `customer_name` onto `CustomerDeal.customer_name` so
    # the tracker_aggregator (which reads `(deal.customer_name) or
    # call.customer_name`) sees the new value. Without this, a
    # customer-name-only PATCH never loaded `deal`, the dual-write
    # silently no-op'd, and the tracker kept showing the stale
    # "(pending audio upload)" placeholder. A call without a linked
    # deal is allowed (it'll just write Call.customer_name and skip
    # the deal-side write at line ~479).
    needs_deal = (
        any(loc in ("deal", "both") for loc in accepted.values())
        or "customer_name" in accepted
    )
    deal: CustomerDeal | None = None
    if needs_deal:
        if not call.deal_id:
            # When the only deal-touching field is customer_name and the
            # call has no linked deal, that's not an error — there's just
            # nothing to dual-write. Only deal-level edits (MPAN, value,
            # term, etc) genuinely require a deal to exist.
            if any(loc in ("deal", "both") for loc in accepted.values()):
                raise HTTPException(
                    400, "deal-level edits unavailable — call has no linked deal"
                )
        else:
            deal = (
                db.query(CustomerDeal).filter(CustomerDeal.id == call.deal_id).first()
            )
            if deal is None and any(
                loc in ("deal", "both") for loc in accepted.values()
            ):
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
                _record_reviewer_edit(
                    db,
                    rejection_id=None,
                    call_id=str(call.id),
                    field="call.detected_supplier",
                    old_value=old_call,
                    new_value=v,
                    reviewer_id=reviewer_id,
                )
                call.detected_supplier = v
            if deal is not None:
                old_deal = deal.supplier
                if old_deal != v:
                    _record_reviewer_edit(
                        db,
                        rejection_id=None,
                        call_id=str(call.id),
                        field="deal.supplier",
                        old_value=old_deal,
                        new_value=v,
                        reviewer_id=reviewer_id,
                    )
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
                _record_reviewer_edit(
                    db,
                    rejection_id=None,
                    call_id=str(call.id),
                    field=f"call.{k}",
                    old_value=old,
                    new_value=v,
                    reviewer_id=reviewer_id,
                )
                setattr(call, k, v)
            # 2026-05-24 — dual-write customer_name to the linked deal.
            # The tracker_aggregator reads ``(deal.customer_name) or
            # call.customer_name`` (line 182 / 350 / 406), so editing the
            # Call alone leaves the tracker rendering the old deal value
            # ("(pending audio upload)" placeholder is the common case).
            # When the reviewer types the real customer name on the side
            # panel, the intent is "this IS the customer" — propagate to
            # the deal so every tracker tab + the /deals + /customers
            # surfaces update on the same query invalidation.
            if k == "customer_name" and deal is not None:
                old_deal = deal.customer_name
                if old_deal != v:
                    _record_reviewer_edit(
                        db,
                        rejection_id=None,
                        call_id=str(call.id),
                        field="deal.customer_name",
                        old_value=old_deal,
                        new_value=v,
                        reviewer_id=reviewer_id,
                    )
                    deal.customer_name = v
                    if hasattr(deal, "field_sources"):
                        fs = dict(deal.field_sources or {})
                        fs["customer_name"] = "reviewer_edit"
                        deal.field_sources = fs
            continue

        # loc == "deal"
        v = _coerce_value(k, raw_v)
        old = getattr(deal, k, None) if deal is not None else None
        if deal is not None and old != v:
            _record_reviewer_edit(
                db,
                rejection_id=None,
                call_id=str(call.id),
                field=f"deal.{k}",
                old_value=old,
                new_value=v,
                reviewer_id=reviewer_id,
            )
            setattr(deal, k, v)
            if hasattr(deal, "field_sources"):
                fs = dict(deal.field_sources or {})
                fs[k] = "reviewer_edit"
                deal.field_sources = fs

    try:
        db.commit()
    except (IntegrityError, OperationalError) as exc:
        db.rollback()
        raise HTTPException(409, f"call metadata update failed: {exc.__class__.__name__}")
    db.refresh(call)
    # Realtime fan-out so other tabs see the call-meta edit immediately.
    try:
        from app import realtime
        realtime.publish(
            str(call.id),
            "call_updated",
            {"fields": list(body.keys())},
        )
    except Exception:
        pass
    return {
        "call_id": str(call.id),
        "deal_id": str(deal.id) if deal else None,
        "deal_field_sources": dict(deal.field_sources or {}) if deal else None,
        "applied_keys": list(accepted.keys()),
    }
