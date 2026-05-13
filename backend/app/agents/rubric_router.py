"""Rubric Router — pick which rule set applies to a given call OR segment.

2026-05-12 taxonomy rebuild — locked to 4 call_type values:
``{lead_gen, pre_sales, verbal, loa}``. The old vocabulary
(passover, closer, c_call, amendment, full, standalone_loa) is gone.

Two entry points:

  - ``route(call, db)`` — call-level routing (back-compat for callers
    that haven't been updated to per-segment yet). Reads ``call.call_type``.

  - ``route_for_segment(segment_type, call, db)`` — per-segment routing
    used by the new content-classifier-based pipeline. Decoupled from
    the Call row's own call_type so a single recording with multiple
    segments can route each to its own rubric.

Rubric mapping (both functions converge here):

    lead_gen  → phrase-pack lead_gen (88 Watt rules)
    pre_sales → phrase-pack pre_sales (88 Watt rules — SAME rule set as
                lead_gen per Aly: different content, identical rules)
    verbal    → supplier-specific verbal-contract script (E.ON NHH+HH
                = 26 cps; British Gas Acquisition = 21 cps; …)
    loa       → supplier-specific LOA script (E.ON TPI Verbal LOA = 11
                cps). LOA audio only exists for E.ON; for non-E.ON the
                LOA is always paper/DocuSign and the segment classifier
                should drop any LOA segment before calling us.

The phrase packs are stored as ordinary `Script` rows with a sentinel
``supplier_name == "PHRASE_PACK"`` so the analyzer's existing checkpoint
JSON loader path works without a schema change.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.logger import log
from app.models import Call, Script


# Marker we set on the synthetic phrase-pack Script rows.
PHRASE_PACK_SUPPLIER = "PHRASE_PACK"

# Maps each call_type or segment_type to the lifecycle_phase the
# phrase-pack Script row should advertise. Pre-sales uses the lead_gen
# pack per user spec (same 88 rules grade both — different content).
_PHRASE_PACK_PHASE: dict[str, str] = {
    "lead_gen": "lead_gen",
    "pre_sales": "lead_gen",
}

# Segment types that grade against a supplier-specific verbal-contract
# script (the closer's binding contract reading).
_VERBAL_SEGMENT_TYPES: set[str] = {"verbal"}

# Segment types that grade against a supplier-specific LOA script.
_LOA_SEGMENT_TYPES: set[str] = {"loa"}


@dataclass(frozen=True)
class Rubric:
    """The single decision the analyzer needs.

    Attributes:
        kind: one of "script_checkpoints" (supplier verbal),
              "loa_script", "phrase_pack", "fallback_v1".
        script: the Script row whose `checkpoints` JSON the analyzer
                grades against. None for "fallback_v1".
        reason: one-line audit explanation.
        call_type: echo of the call_type or segment_type that led here.
    """

    kind: str
    script: Script | None
    reason: str
    call_type: str | None


def _resolve_phrase_pack(db: Session, phase: str) -> Script | None:
    """Look up the phrase-pack Script row for a given phase. Case-insensitive
    on supplier so seed mishaps don't blow this up.
    """
    return (
        db.query(Script)
        .filter(Script.supplier_name.ilike(PHRASE_PACK_SUPPLIER))
        .filter(Script.lifecycle_phase == phase)
        .filter(Script.active == True)  # noqa: E712
        .first()
    )


def _resolve_loa_script(db: Session, supplier: str | None) -> Script | None:
    """Pick the LOA script for the call's supplier. Only E.ON has one
    in the seeded corpus (lifecycle_phase='loa' or 'standalone_loa').
    """
    q = db.query(Script).filter(Script.active == True)  # noqa: E712
    q = q.filter(Script.lifecycle_phase.in_(("loa", "standalone_loa")))
    if supplier:
        q = q.filter(Script.supplier_name.ilike(f"%{supplier.split()[0]}%"))
    return q.first()


def _resolve_for(
    segment_type: str,
    call: Call,
    db: Session,
) -> Rubric:
    """Shared routing core used by both ``route`` and ``route_for_segment``."""
    sg = (segment_type or "").strip().lower()

    # Verbal — supplier-specific verbal-contract checkpoints attached
    # to the call as call.script_id by detect_metadata.
    if sg in _VERBAL_SEGMENT_TYPES:
        if call.script_id:
            script = db.query(Script).filter_by(id=call.script_id).first()
            if script:
                try:
                    cp_count = len(json.loads(script.checkpoints or "[]") or [])
                except Exception:
                    cp_count = 0
                if cp_count > 0:
                    return Rubric(
                        kind="script_checkpoints",
                        script=script,
                        reason=(
                            f"segment={sg!r} → supplier-script "
                            f"\"{script.script_name}\" ({cp_count} cps)"
                        ),
                        call_type=sg,
                    )
                log.warning(
                    f"📋 ROUTER verbal segment has script with empty "
                    f"checkpoints (script_id={call.script_id})"
                )

    # LOA — supplier-specific LOA script. Only E.ON has one in the
    # seeded corpus today.
    if sg in _LOA_SEGMENT_TYPES:
        loa = _resolve_loa_script(db, getattr(call, "detected_supplier", None))
        if loa and loa.checkpoints and loa.checkpoints != "[]":
            cp_count = len(json.loads(loa.checkpoints) or [])
            return Rubric(
                kind="loa_script",
                script=loa,
                reason=(
                    f"segment={sg!r} → LOA script "
                    f"\"{loa.script_name}\" ({cp_count} cps)"
                ),
                call_type=sg,
            )

    # Lead-gen / Pre-sales — phrase pack (both share the 88-rule
    # lead_gen pack per Aly's spec).
    pack_phase = _PHRASE_PACK_PHASE.get(sg)
    if pack_phase:
        pack = _resolve_phrase_pack(db, pack_phase)
        if pack and pack.checkpoints and pack.checkpoints != "[]":
            cp_count = len(json.loads(pack.checkpoints) or [])
            return Rubric(
                kind="phrase_pack",
                script=pack,
                reason=(
                    f"segment={sg!r} → phrase-pack/{pack_phase} "
                    f"({cp_count} rules)"
                ),
                call_type=sg,
            )

    # Nothing matched — surface explicit fallback so reviewer sees it.
    return Rubric(
        kind="fallback_v1",
        script=None,
        reason=(
            f"segment={sg!r} → no script + no phrase pack matched; "
            "falling through to V1 third-party-disclosure analyzer"
        ),
        call_type=sg,
    )


def route(call: Call, db: Session) -> Rubric:
    """Call-level routing — picks one rubric for the WHOLE recording
    based on ``call.call_type``. Back-compat path; the new pipeline
    uses ``route_for_segment`` instead so a single recording can have
    multiple rubrics applied to different segments.
    """
    ct = (call.call_type or "").strip().lower()
    return _resolve_for(ct, call, db)


def route_for_segment(
    segment_type: str,
    call: Call,
    db: Session,
) -> Rubric:
    """Per-segment routing — used by the new pipeline.

    The segment_type comes from the content_classifier agent's output,
    NOT from call.call_type. So a single recording classified as 'verbal'
    overall can still have a 'pre_sales' segment at the start that
    grades against the phrase pack.
    """
    return _resolve_for(segment_type, call, db)
