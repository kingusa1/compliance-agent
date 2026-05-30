"""Saved views for FilterableArchive (L4).

Distinct from the legacy /api/views (queue presets). L4 saved views target
the FilterableArchive surface (findings / compliant / non-compliant /
agent drilldowns) and carry an `endpoint` discriminator so a "Critical
FAILs on /findings" view doesn't appear in a /compliant dropdown.

Filter shape is allow-listed by `FilterShape` Pydantic — unknown keys
return 422 at write-time. Read-time, we re-validate so an old view written
with a now-deprecated key surfaces only the keys still allow-listed.

Storage rides the existing `saved_views` table (Task 26): `name`,
`filters` (JSON text), `is_shared`, `owner_id`, `created_at`. We stash
the `endpoint` discriminator inside the filters JSON under the reserved
key `__endpoint__` so no schema migration is required.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from app.auth import current_user
from app.database import get_db
from app.logger import log
from app.models import SavedView

# Auth gate (2026-05-30 security audit): saved-view CRUD is per-user state —
# require an authenticated user.
saved_views_router = APIRouter(
    prefix="/api/saved-views",
    tags=["saved-views"],
    dependencies=[Depends(current_user)],
)

# Allow-listed filter keys. Mirrors FilterChips on the frontend.
_ALLOWED_KEYS: set[str] = {
    "agent_name",
    "supplier",
    "risk_tag",
    "fix_status",
    "rejection_category",
    "date_from",
    "date_to",
    "lifecycle_status",
    "deal_value_bucket",
    "supplier_campaign",
    # legacy keys also accepted so older views still load
    "status",
    "compliant",
}

# Reserved key inside filters JSON used to stash endpoint discriminator.
_ENDPOINT_KEY = "__endpoint__"


# ── schemas ─────────────────────────────────────────────────────────────

class FilterShape(BaseModel):
    """Allow-listed filter dictionary. Unknown keys → 422."""

    # Inherit nothing — we hand-roll allow-listing because filter values are
    # all str|None and we don't want each one written out as a Field().
    model_config = {"extra": "forbid"}

    agent_name: str | None = None
    supplier: str | None = None
    risk_tag: str | None = None
    fix_status: str | None = None
    rejection_category: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    lifecycle_status: str | None = None
    deal_value_bucket: str | None = None
    supplier_campaign: str | None = None
    status: str | None = None
    compliant: str | None = None


class SavedViewCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    endpoint: str = Field(min_length=1, max_length=300)
    filters: dict[str, Any]


class SavedViewUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    filters: dict[str, Any] | None = None


class SavedViewOut(BaseModel):
    id: str
    name: str
    endpoint: str
    filters: dict[str, str]
    owner_id: str | None
    created_at: datetime | None


# ── helpers ─────────────────────────────────────────────────────────────

def _validate_filters(raw: dict[str, Any]) -> dict[str, str]:
    """Validate write-time + sanitise read-time. Unknown keys → 422.

    Returns the filtered dict with all values coerced to strings (empty
    values dropped) so it's safe to write straight into the saved view's
    JSON column.
    """
    # FilterShape rejects unknown keys with extra="forbid".
    try:
        FilterShape(**{k: v for k, v in raw.items() if k != _ENDPOINT_KEY})
    except ValidationError as e:
        raise HTTPException(422, f"invalid filter keys: {e.errors()[0]['msg']}")
    out: dict[str, str] = {}
    for k, v in raw.items():
        if k == _ENDPOINT_KEY:
            continue
        if k not in _ALLOWED_KEYS:
            continue
        if v is None:
            continue
        s = str(v).strip()
        if s:
            out[k] = s
    return out


def _serialize(v: SavedView) -> SavedViewOut:
    raw = json.loads(v.filters) if isinstance(v.filters, str) else (v.filters or {})
    endpoint = raw.pop(_ENDPOINT_KEY, "") if isinstance(raw, dict) else ""
    # Read-time sanitise: drop now-disallowed keys silently. (Audit
    # design_decision: "saved_views_validation: filters JSON write-time
    # validated; unknown keys dropped on read".)
    filters = {k: str(val) for k, val in (raw.items() if isinstance(raw, dict) else []) if k in _ALLOWED_KEYS and val}
    return SavedViewOut(
        id=str(v.id),
        name=v.name,
        endpoint=endpoint or "",
        filters=filters,
        owner_id=v.owner_id,
        created_at=v.created_at,
    )


# ── routes ──────────────────────────────────────────────────────────────

@saved_views_router.get("")
def list_saved_views(
    endpoint: str | None = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    rows = db.query(SavedView).order_by(SavedView.created_at.desc()).all()
    out = [_serialize(r) for r in rows]
    if endpoint:
        out = [v for v in out if v.endpoint == endpoint]
    return {"views": [v.model_dump(mode="json") for v in out]}


@saved_views_router.post("", status_code=201)
def create_saved_view(payload: SavedViewCreate, db: Session = Depends(get_db)) -> dict:
    filters = _validate_filters(payload.filters)
    stored = {**filters, _ENDPOINT_KEY: payload.endpoint}
    v = SavedView(
        id=str(uuid.uuid4()),
        owner_id="",  # filled in once we wire auth context here; harmless empty
        name=payload.name,
        filters=json.dumps(stored),
        is_shared=False,
    )
    db.add(v)
    db.commit()
    db.refresh(v)
    log.info(f"SAVED_VIEW_CREATE id={v.id} endpoint={payload.endpoint!r} name={payload.name!r}")
    return _serialize(v).model_dump(mode="json")


@saved_views_router.patch("/{view_id}")
def patch_saved_view(view_id: str, payload: SavedViewUpdate, db: Session = Depends(get_db)) -> dict:
    v = db.query(SavedView).filter(SavedView.id == view_id).one_or_none()
    if not v:
        raise HTTPException(404, "saved view not found")
    if payload.name is not None:
        v.name = payload.name
    if payload.filters is not None:
        validated = _validate_filters(payload.filters)
        # Preserve endpoint stamp if the patch didn't include one.
        existing = json.loads(v.filters) if isinstance(v.filters, str) else (v.filters or {})
        endpoint = existing.get(_ENDPOINT_KEY, "")
        v.filters = json.dumps({**validated, _ENDPOINT_KEY: endpoint})
    db.commit()
    db.refresh(v)
    return _serialize(v).model_dump(mode="json")


@saved_views_router.delete("/{view_id}")
def delete_saved_view(view_id: str, db: Session = Depends(get_db)) -> dict:
    v = db.query(SavedView).filter(SavedView.id == view_id).one_or_none()
    if not v:
        raise HTTPException(404, "saved view not found")
    db.delete(v)
    db.commit()
    return {"deleted": True}


__all__ = ["saved_views_router"]
