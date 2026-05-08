"""Reviewer-raised flags + cross-call findings archive (L4).

Two endpoints:

  • POST /api/calls/{call_id}/flags  — reviewer adds a flag the AI missed
    (drag-select transcript → modal → submit). Source is stamped
    "reviewer" so the L2 auto-flags stay separate.

  • GET  /api/findings                — cross-call flag list with offset
    pagination + filter chips (agent_name, supplier, risk_tag,
    rejection_category, fix_status, lifecycle_status, date_from/to).

The findings query joins `flags` against `calls` and `customer_deals` for
the columns FilterableArchive needs in one round-trip. Filters are
allow-listed at the query layer; unknown query params are silently
ignored to keep curl-friendly callers stable.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from app.database import get_db
from app.logger import log
from app.models import Call, Flag

flags_router = APIRouter(tags=["flags"])


# ── schemas ─────────────────────────────────────────────────────────────

class FlagCreate(BaseModel):
    rule_id: str = Field(min_length=1, max_length=120)
    severity: str = Field(pattern="^(critical|high|medium)$")
    reason: str = Field(min_length=1)
    word_start: int = Field(ge=0)
    word_end: int = Field(ge=0)
    evidence: str | None = None
    risk_tag: str | None = Field(default=None, pattern=r"^(ombudsman|mis-selling|complaint|cancellation)?$")


class FlagOut(BaseModel):
    id: str
    call_id: str
    rule_id: str
    severity: str
    reason: str | None
    evidence: str | None
    word_start: int | None
    word_end: int | None
    risk_tag: str | None
    source: str
    created_by_id: str | None
    created_at: datetime | None


def _serialize_flag(f: Flag) -> FlagOut:
    return FlagOut(
        id=str(f.id),
        call_id=str(f.call_id),
        rule_id=f.rule_id,
        severity=f.severity,
        reason=f.reason,
        evidence=f.evidence,
        word_start=f.word_start,
        word_end=f.word_end,
        risk_tag=f.risk_tag,
        source=f.source or "reviewer",
        created_by_id=f.created_by_id,
        created_at=f.created_at,
    )


# ── routes ──────────────────────────────────────────────────────────────

@flags_router.post("/api/calls/{call_id}/flags", status_code=201)
def create_flag(call_id: str, payload: FlagCreate, db: Session = Depends(get_db)) -> dict:
    call = db.query(Call).filter(Call.id == call_id).one_or_none()
    if not call:
        raise HTTPException(404, "call not found")
    if payload.word_end < payload.word_start:
        raise HTTPException(422, "word_end must be >= word_start")

    f = Flag(
        call_id=call_id,
        rule_id=payload.rule_id,
        severity=payload.severity,
        reason=payload.reason,
        evidence=payload.evidence,
        word_start=payload.word_start,
        word_end=payload.word_end,
        risk_tag=payload.risk_tag or None,
        source="reviewer",
    )
    db.add(f)
    db.commit()
    db.refresh(f)
    log.info(
        f"FLAG_CREATED id={f.id} call_id={call_id} rule={payload.rule_id} "
        f"severity={payload.severity} word_range=[{payload.word_start},{payload.word_end}]"
    )
    return {"flag": _serialize_flag(f).model_dump(mode="json")}


@flags_router.get("/api/calls/{call_id}/flags")
def list_call_flags(call_id: str, db: Session = Depends(get_db)) -> dict:
    rows = (
        db.query(Flag)
        .filter(Flag.call_id == call_id)
        .order_by(Flag.created_at.desc())
        .all()
    )
    return {"flags": [_serialize_flag(r).model_dump(mode="json") for r in rows]}


# ── findings (cross-call) ───────────────────────────────────────────────

# Allow-listed filter keys. Unknown query params dropped silently so curl
# callers stay forgiving.
_FILTER_COLUMNS = {
    "agent_name": "c.agent_name",
    "supplier": "COALESCE(d.supplier, c.detected_supplier)",
    "risk_tag": "f.risk_tag",
    "rejection_category": "d.rejection_category",
    "lifecycle_status": "d.status",
}


@flags_router.get("/api/findings")
def list_findings(
    agent_name: str | None = Query(None),
    supplier: str | None = Query(None),
    risk_tag: str | None = Query(None),
    rejection_category: str | None = Query(None),
    lifecycle_status: str | None = Query(None),
    fix_status: str | None = Query(None),  # joins fix_directives.status
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    conds: list[str] = []
    params: dict[str, Any] = {"limit": limit, "offset": offset}

    if agent_name:
        conds.append("c.agent_name = :agent_name")
        params["agent_name"] = agent_name
    if supplier:
        conds.append("COALESCE(d.supplier, c.detected_supplier) = :supplier")
        params["supplier"] = supplier
    if risk_tag:
        conds.append("f.risk_tag = :risk_tag")
        params["risk_tag"] = risk_tag
    if rejection_category:
        # Audit Fix #13 alias: COMPLIANCE ERROR → COMPLIANCE ISSUE.
        canonical = "COMPLIANCE ISSUE" if rejection_category == "COMPLIANCE ERROR" else rejection_category
        conds.append("d.rejection_category = :rejection_category")
        params["rejection_category"] = canonical
    if lifecycle_status:
        conds.append("d.status = :lifecycle_status")
        params["lifecycle_status"] = lifecycle_status
    if date_from:
        conds.append("f.created_at >= :date_from")
        params["date_from"] = date_from
    if date_to:
        conds.append("f.created_at <= :date_to")
        params["date_to"] = date_to

    fix_join = ""
    if fix_status:
        # Audit Fix #15: 5-state taxonomy with 'submitted' added.
        fix_join = "LEFT JOIN fix_directives fd ON fd.call_id = c.id"
        conds.append("fd.status = :fix_status")
        params["fix_status"] = fix_status

    where_sql = ("WHERE " + " AND ".join(conds)) if conds else ""

    sql = f"""
        SELECT
            f.id::text                                               AS id,
            f.call_id                                                AS call_id,
            f.rule_id                                                AS rule_id,
            f.severity                                               AS severity,
            f.reason                                                 AS reason,
            f.evidence                                               AS evidence,
            f.risk_tag                                               AS risk_tag,
            f.source                                                 AS source,
            c.agent_name                                             AS agent_name,
            COALESCE(d.customer_name, c.customer_name)               AS customer_name,
            COALESCE(d.supplier, c.detected_supplier)                AS supplier,
            d.rejection_category                                     AS rejection_category,
            {("fd.status" if fix_status else "NULL")}                AS fix_status,
            d.status                                                 AS lifecycle_status,
            f.created_at                                             AS created_at,
            COUNT(*) OVER()                                          AS total_count
        FROM flags f
        JOIN calls c               ON c.id = f.call_id
        LEFT JOIN customer_deals d ON d.id = c.deal_id
        {fix_join}
        {where_sql}
        ORDER BY f.created_at DESC NULLS LAST
        LIMIT :limit OFFSET :offset
    """

    try:
        rows = db.execute(text(sql), params).fetchall()
    except (OperationalError, ProgrammingError) as e:
        # `flags` / `customer_deals` may be missing in older test DBs — treat
        # absent tables as "no findings yet" rather than 500 the page.
        log.warning(f"findings query failed gracefully: {e}")
        return {"findings": [], "total": 0, "has_more": False}

    total = int(rows[0].total_count) if rows else 0
    findings: list[dict[str, Any]] = []
    for r in rows:
        findings.append({
            "id": r.id,
            "call_id": r.call_id,
            "rule_id": r.rule_id,
            "severity": r.severity,
            "reason": r.reason,
            "evidence": r.evidence,
            "risk_tag": r.risk_tag,
            "source": r.source,
            "agent_name": r.agent_name,
            "customer_name": r.customer_name,
            "supplier": r.supplier,
            "rejection_category": r.rejection_category,
            "fix_status": r.fix_status,
            "lifecycle_status": r.lifecycle_status,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })
    return {
        "findings": findings,
        "total": total,
        "has_more": (offset + len(findings)) < total,
    }


__all__ = ["flags_router"]
