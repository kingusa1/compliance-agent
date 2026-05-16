"""Reviewer fix-directives + auto-feedback-email endpoints (DEMO-05/06).

Tightly scoped:
  • POST /api/calls/{call_id}/directives        — create pending directive
  • GET  /api/calls/{call_id}/directives        — list directives for call
  • PATCH /api/directives/{id}                  — transition status
  • POST /api/calls/{call_id}/feedback-email    — send auto-email; logs
                                                  FEEDBACK_EMAIL_SEND so
                                                  the demo's terminal log
                                                  shows the event
"""
from __future__ import annotations

from datetime import datetime
from app._clock import utcnow
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.logger import log
from app.models import Call, FixDirective

directives_router = APIRouter(tags=["directives"])

# ── state machine ─────────────────────────────────────────────────

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"in_progress", "dead"},
    "in_progress": {"fixed", "dead"},
    "fixed": set(),  # terminal
    "dead": set(),  # terminal
}


# ── schemas ───────────────────────────────────────────────────────

class DirectiveCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    body: str | None = None


class DirectivePatch(BaseModel):
    status: str = Field(pattern="^(pending|in_progress|fixed|dead)$")


class DirectiveOut(BaseModel):
    id: str
    call_id: str
    title: str
    body: str | None
    status: str
    created_at: datetime | None
    updated_at: datetime | None
    fixed_at: datetime | None


def _serialize(d: FixDirective) -> DirectiveOut:
    return DirectiveOut(
        id=str(d.id),
        call_id=d.call_id,
        title=d.title,
        body=d.body,
        status=d.status,
        created_at=d.created_at,
        updated_at=d.updated_at,
        fixed_at=d.fixed_at,
    )


# ── routes ────────────────────────────────────────────────────────

@directives_router.post(
    "/api/calls/{call_id}/directives",
    response_model=DirectiveOut,
    status_code=201,
)
def create_directive(
    call_id: str,
    payload: DirectiveCreate,
    db: Session = Depends(get_db),
) -> DirectiveOut:
    call = db.query(Call).filter(Call.id == call_id).one_or_none()
    if not call:
        raise HTTPException(404, "call not found")
    d = FixDirective(call_id=call_id, title=payload.title, body=payload.body, status="pending")
    db.add(d)
    db.commit()
    db.refresh(d)
    log.info(f"FIX_DIRECTIVE created id={d.id} call_id={call_id} title={payload.title!r}")
    return _serialize(d)


@directives_router.get("/api/calls/{call_id}/directives")
def list_directives(call_id: str, db: Session = Depends(get_db)) -> dict:
    rows = (
        db.query(FixDirective)
        .filter(FixDirective.call_id == call_id)
        .order_by(FixDirective.created_at.desc())
        .all()
    )
    return {"directives": [_serialize(r).model_dump(mode="json") for r in rows]}


@directives_router.patch("/api/directives/{directive_id}", response_model=DirectiveOut)
def patch_directive(
    directive_id: UUID,
    payload: DirectivePatch,
    db: Session = Depends(get_db),
) -> DirectiveOut:
    d = db.query(FixDirective).filter(FixDirective.id == directive_id).one_or_none()
    if not d:
        raise HTTPException(404, "directive not found")
    new_status = payload.status
    if new_status == d.status:
        return _serialize(d)
    if new_status not in ALLOWED_TRANSITIONS.get(d.status, set()):
        raise HTTPException(
            422,
            f"invalid transition {d.status} -> {new_status}; allowed: {sorted(ALLOWED_TRANSITIONS.get(d.status, set())) or 'terminal'}",
        )
    d.status = new_status
    d.updated_at = utcnow()
    if new_status == "fixed":
        d.fixed_at = d.updated_at
    db.commit()
    db.refresh(d)
    log.info(f"FIX_DIRECTIVE transitioned id={d.id} -> {new_status}")
    return _serialize(d)


# ── feedback email (DEMO-06) ──────────────────────────────────────

class FeedbackEmailIn(BaseModel):
    to_addr: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    subject: str = Field(min_length=1, max_length=300)
    body_markdown: str


@directives_router.post("/api/calls/{call_id}/feedback-email")
def send_feedback_email(
    call_id: str,
    payload: FeedbackEmailIn,
    db: Session = Depends(get_db),
) -> dict:
    """Stub send — logs FEEDBACK_EMAIL_SEND so the demo can show the
    terminal event without wiring a real provider yet. The reviewer
    workflow is identical: edit body, click Send, see the event."""
    call = db.query(Call).filter(Call.id == call_id).one_or_none()
    if not call:
        raise HTTPException(404, "call not found")
    log.info(
        f"FEEDBACK_EMAIL_SEND call_id={call_id} to={payload.to_addr} "
        f"subject={payload.subject!r} body_chars={len(payload.body_markdown)}"
    )
    return {"ok": True, "to": payload.to_addr, "subject": payload.subject}
