"""Rubric Router — pick which rule set applies to a given call.

The pipeline used to load `call.script_id` unconditionally and grade
EVERY call against the closer-script's verbal-contract checkpoints. That
produced false-fails on lead-gen calls (the agent never reads the closer
script during qualification) and on standalone-LOA calls (same reason).

Aly's rule (from the Watt Sales Compliance Guide § Phrase Detection
Dataset): lead-gen, passover, c-call, amendment recordings must be
graded against PHRASE PACKS (88 Watt lead-gen rules + 32 verbal rules);
only verbal-contract recordings (closer / verbal / full) should be
graded against the supplier-specific verbal-contract checkpoints.

This module owns that routing decision. Returns a single `Rubric` that
the analyzer step consumes — the analyzer no longer has to know about
phrase packs vs script checkpoints.

The phrase packs are stored as ordinary `Script` rows with a sentinel
``supplier_name == "PHRASE_PACK"`` so the existing analyzer + checkpoint
JSON loader path works without a schema change.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.logger import log
from app.models import Call, Script


# Marker we set on the synthetic phrase-pack Script rows. Keeps them
# discoverable by lifecycle_phase + supplier without colliding with real
# supplier scripts.
PHRASE_PACK_SUPPLIER = "PHRASE_PACK"

# Maps each call_type to the lifecycle_phase the phrase-pack Script row
# should advertise. Used to look up the pack when no supplier-specific
# verbal-contract script applies.
_CALL_TYPE_PHRASE_PACK_PHASE: dict[str, str] = {
    "lead_gen": "lead_gen",
    "passover": "passover",
    "c_call": "c_call",
    "amendment": "amendment",
}

# Verbal-contract call types — these grade against the supplier-specific
# checkpoints already on `call.script_id`.
_VERBAL_CONTRACT_CALL_TYPES: set[str] = {"closer", "verbal", "full"}

# Standalone LOA grades against the supplier's LOA script (e.g. "E.ON TPI
# Verbal LOA"). Different lifecycle_phase tag so it's picked up by
# `_resolve_loa_script`.
_LOA_CALL_TYPES: set[str] = {"standalone_loa", "loa"}


@dataclass(frozen=True)
class Rubric:
    """The single decision the analyzer needs.

    Attributes:
        kind: one of "script_checkpoints", "phrase_pack", "loa_script", "fallback_v1"
        script: the Script row whose `checkpoints` JSON the analyzer should grade against.
                None for "fallback_v1".
        reason: one-line audit explanation written to the agent_trace + log.
        call_type: echo of the call_type that led to this routing decision.
    """

    kind: str
    script: Script | None
    reason: str
    call_type: str | None


def _resolve_phrase_pack(db: Session, phase: str) -> Script | None:
    """Look up the phrase-pack Script row for the given phase. Case-insensitive
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
    """Pick the LOA script for the call's supplier. E.ON has a dedicated
    LOA script (lifecycle_phase='loa'). Other suppliers usually bundle
    the LOA into the closer — in that case we fall through to the
    closer rubric since standalone-LOA is the exception there.
    """
    q = db.query(Script).filter(Script.active == True)  # noqa: E712
    q = q.filter(Script.lifecycle_phase.in_(("loa", "standalone_loa")))
    if supplier:
        q = q.filter(Script.supplier_name.ilike(f"%{supplier.split()[0]}%"))
    return q.first()


def route(call: Call, db: Session) -> Rubric:
    """Decide which rubric applies to this call. Pure read-only — the
    caller persists the result via agent_trace if it wants an audit row.
    """
    ct_raw = (call.call_type or "").strip().lower()
    if not ct_raw:
        ct = "full"  # treat unclassified as full-call → closer rubric
    else:
        ct = ct_raw

    # 1. Verbal contract / closer / full — grade against supplier-specific
    # checkpoints that the script-matcher already attached to the call.
    if ct in _VERBAL_CONTRACT_CALL_TYPES:
        if call.script_id:
            script = db.query(Script).filter_by(id=call.script_id).first()
            if script:
                cp_count = 0
                try:
                    cp_count = len(json.loads(script.checkpoints or "[]") or [])
                except Exception:
                    cp_count = 0
                if cp_count > 0:
                    return Rubric(
                        kind="script_checkpoints",
                        script=script,
                        reason=(
                            f"call_type={ct!r} → supplier-script "
                            f"\"{script.script_name}\" ({cp_count} cps)"
                        ),
                        call_type=ct,
                    )
                # script row exists but has no rules — fall through to
                # phrase pack rather than scoring against nothing.
                log.warning(
                    f"📋 ROUTER closer call has script with empty checkpoints "
                    f"(script_id={call.script_id}); falling through to phrase pack"
                )

    # 2. Standalone LOA — supplier-specific LOA script (only E.ON has one
    # in the seeded corpus today; other suppliers bundle the LOA into the
    # closer so this branch returns no script and we fall through).
    if ct in _LOA_CALL_TYPES:
        loa = _resolve_loa_script(db, call.detected_supplier)
        if loa and loa.checkpoints and loa.checkpoints != "[]":
            cp_count = len(json.loads(loa.checkpoints) or [])
            return Rubric(
                kind="loa_script",
                script=loa,
                reason=(
                    f"call_type={ct!r} → LOA script "
                    f"\"{loa.script_name}\" ({cp_count} cps)"
                ),
                call_type=ct,
            )

    # 3. Lead-gen / passover / c-call / amendment — phrase pack.
    pack_phase = _CALL_TYPE_PHRASE_PACK_PHASE.get(ct, "lead_gen")
    pack = _resolve_phrase_pack(db, pack_phase)
    if pack and pack.checkpoints and pack.checkpoints != "[]":
        cp_count = len(json.loads(pack.checkpoints) or [])
        return Rubric(
            kind="phrase_pack",
            script=pack,
            reason=(
                f"call_type={ct!r} → phrase-pack/{pack_phase} "
                f"({cp_count} rules)"
            ),
            call_type=ct,
        )

    # 4. No script + no phrase pack matched — the analyzer will fall
    # through to V1 third-party-disclosure. We surface it explicitly so
    # the reviewer queue can prioritise these.
    return Rubric(
        kind="fallback_v1",
        script=None,
        reason=(
            f"call_type={ct!r} → no script + no phrase pack matched; "
            "falling through to V1 third-party-disclosure analyzer"
        ),
        call_type=ct,
    )
