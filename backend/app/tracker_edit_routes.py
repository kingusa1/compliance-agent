"""PATCH /api/tracker/rows/{id} — inline edit any whitelisted field on a
tracker row (Rejection). Each successful edit:
  - flips field_sources[<field>] = "human"
  - writes a ReviewerEdit audit row
  - returns the fresh field_sources so the frontend can drop the AI badge.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.field_sources import set_source
from app.models import Rejection, ReviewerEdit
from app.reviewers import current_reviewer


tracker_edit_router = APIRouter()


# Whitelisted fields. Fields not in this set 400 — protects FK columns
# (call_id, customer_slug) and timestamps (created_at, rejected_at) from
# arbitrary client overwrites.
ALLOWED_FIELDS = {
    "supplier",
    "sales_agent",
    "category",
    "rejection_reason",
    "fix_required",
    "fix_narrative",
    "status",
    "outcome",
    "outcome_narrative",
}


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

    reviewer_id = str(user.get("id")) if isinstance(user, dict) else None
    for k, v in body.items():
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
    db.commit()
    db.refresh(rej)
    return {"id": str(rej.id), "field_sources": rej.field_sources}
