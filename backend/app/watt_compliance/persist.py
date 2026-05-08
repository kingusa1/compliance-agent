"""Persist a Watt-grounded analysis result to the DB.

Bridges the JSON dict returned by ``app/analysis.py:analyze_compliance_watt``
to:

- ``Call.compliance_status``     ← ``compliance_status`` (compliant / non_compliant)
- ``Call.score``                 ← ``score``/100 (kept as string to match the
                                   existing "passed/total" convention)
- ``Call.reason``                ← ``summary`` (reviewer-facing one-liner)
- ``Call.risk_tags``             ← canonical tags (already normalised upstream)
- ``Rejection`` rows             ← one per item in ``rejections``

The function is **idempotent on call_id**: re-running it deletes prior
``Rejection`` rows for that call before inserting fresh ones. Callers are
responsible for the ``db.commit()`` so the whole pipeline step lands as a
single transaction.

Usage from a pipeline step / route:

    from app.watt_compliance.persist import persist_watt_analysis
    result = await analyze_compliance_watt(transcript, call_type=ct)
    persist_watt_analysis(call=call, analysis=result, db=db)
    db.commit()
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.watt_compliance.risk_tags import normalize_risk_tags
from app.watt_compliance.taxonomy import (
    REJECTION_REASONS_BY_CODE,
    RejectionCategory,
    Severity,
    TrackerStatus,
)

log = logging.getLogger(__name__)


_VERDICT_TO_COMPLIANCE_STATUS: dict[str, str] = {
    "PASS": "compliant",
    "COACH": "compliant",   # coaching-only call still ships
    "REVIEW": "non_compliant",
    "BLOCK": "non_compliant",
}


def _coerce_score(value: Any) -> str | None:
    """Watt JSON delivers an int 0-100. Call.score is text. Format as
    ``"<score>/100"`` so existing ``parse_score()`` callers (e.g.
    ``app/compliance.py:830``) keep working unmodified."""
    if value is None:
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    n = max(0, min(100, n))
    return f"{n}/100"


def _coerce_category(raw: Any) -> str | None:
    """Validate the inbound master-category string against the enum.
    Unknown values fall back to None so the caller can decide whether
    to skip the row or substitute a default."""
    if isinstance(raw, RejectionCategory):
        return raw.value
    if not isinstance(raw, str):
        return None
    raw = raw.upper().replace(" ", "_").replace("-", "_")
    try:
        return RejectionCategory(raw).value
    except ValueError:
        return None


def _coerce_severity(raw: Any) -> str:
    """Severity values that survive into the DB (today stored on
    Rejection.fix_narrative for now since there's no severity column).
    Default to HIGH so unknown values land in REVIEW not COACH."""
    if isinstance(raw, Severity):
        return raw.value
    if not isinstance(raw, str):
        return Severity.HIGH.value
    raw = raw.upper()
    try:
        return Severity(raw).value
    except ValueError:
        return Severity.HIGH.value


def _build_rejection_reason_text(rejection: dict) -> str:
    """Produce the ``Rejection.rejection_reason`` text in the ops-team
    house style: ``"<R-code> — <title>: <evidence>"``.

    Falls back gracefully if the LLM omitted any field.
    """
    code = str(rejection.get("reason_code") or "?")
    title = ""
    spec = REJECTION_REASONS_BY_CODE.get(code)
    if spec is not None:
        title = spec.title
    evidence = str(rejection.get("evidence_quote") or "").strip()
    parts = [code]
    if title:
        parts.append(f"— {title}")
    if evidence:
        parts.append(f": {evidence}")
    return " ".join(parts)


def persist_watt_analysis(
    *,
    call,
    analysis: dict,
    db: Session,
    rejected_at: datetime | None = None,
) -> dict:
    """Apply ``analysis`` to ``call`` + insert ``Rejection`` rows.

    Returns a small summary dict of what was written. Does NOT commit —
    the caller owns the transaction boundary.
    """
    if call is None:
        raise ValueError("call must not be None")
    if not isinstance(analysis, dict):
        raise TypeError("analysis must be a dict")

    # Late import to dodge SQLAlchemy circular-import issues at module load.
    from app.models import Rejection

    verdict = str(analysis.get("verdict", "")).upper()
    summary = analysis.get("summary")

    # ── Update the Call row ────────────────────────────────────────
    compliance_status = analysis.get("compliance_status")
    if not compliance_status and verdict in _VERDICT_TO_COMPLIANCE_STATUS:
        compliance_status = _VERDICT_TO_COMPLIANCE_STATUS[verdict]
    if compliance_status:
        call.compliance_status = compliance_status

    score_text = _coerce_score(analysis.get("score"))
    if score_text is not None:
        call.score = score_text

    if summary:
        call.reason = str(summary)

    risk_tags = normalize_risk_tags(analysis.get("risk_tags") or [])
    if risk_tags:
        call.risk_tags = risk_tags

    # supplier_detected is informational; we do NOT overwrite Call.detected_supplier
    # if the upstream extractor already filled it (those signals are stronger).
    if analysis.get("supplier_detected") and not getattr(call, "detected_supplier", None):
        call.detected_supplier = analysis["supplier_detected"]

    # ── Replace Rejection rows for this call (idempotent) ──────────
    deleted = (
        db.query(Rejection)
        .filter(Rejection.call_id == str(call.id))
        .delete(synchronize_session=False)
    )

    rejections_in = analysis.get("rejections") or []
    rejected_at = rejected_at or datetime.now(timezone.utc)
    written = 0
    skipped = 0
    for r in rejections_in:
        if not isinstance(r, dict):
            skipped += 1
            continue
        category = _coerce_category(r.get("category"))
        if category is None:
            log.debug("persist_watt_analysis dropping rejection — unknown category: %r",
                      r.get("category"))
            skipped += 1
            continue
        # Severity goes into fix_narrative for now (the existing schema
        # doesn't have a severity column on Rejection — see PHASE2-PLAN
        # §P1.7 if the user wants it added later).
        severity = _coerce_severity(r.get("severity"))

        row = Rejection(
            call_id=str(call.id),
            customer_slug=getattr(call, "customer_slug", None),
            supplier=analysis.get("supplier_detected"),
            sales_agent=getattr(call, "agent_name", None),
            category=category,
            rejection_reason=_build_rejection_reason_text(r),
            fix_required=str(r.get("fix_required") or "")[:500] or None,
            fix_narrative=f"severity={severity}",
            status=TrackerStatus.NOT_STARTED.value.upper(),  # "NOT_STARTED"
            rejected_at=rejected_at,
        )
        db.add(row)
        written += 1

    return {
        "call_id": str(call.id),
        "verdict": verdict,
        "compliance_status": getattr(call, "compliance_status", None),
        "score": getattr(call, "score", None),
        "rejections_written": written,
        "rejections_skipped": skipped,
        "rejections_deleted": deleted,
        "risk_tags": list(getattr(call, "risk_tags", []) or []),
    }
