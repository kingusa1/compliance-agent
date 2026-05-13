"""Watt deal lifecycle state machine — 2026-05-12 taxonomy rebuild.

Maps the canonical 4-stage Watt sales workflow:

    1 customer → 1 deal → 1+ recordings.
    Each recording is classified as call_type ∈ {lead_gen, pre_sales, verbal, loa}.

Aly's mental model (= Watt operations):
    - E.ON Next: 3 stages (Lead Gen, Pre-Sales, Verbal). LOA wording is
                 bundled INSIDE the Verbal segment of the closer
                 recording per the E.ON Next verbal-contract script.
    - All other suppliers: 3 stages (Lead Gen, Pre-Sales, Verbal). LOA
                 is always paper / DocuSign for non-E.ON — never audio.

Per-stage rule:
    - "Latest call per phase wins" — re-records supersede earlier
      non-compliant calls. If lead_gen #1 was non_compliant and
      lead_gen #2 is compliant, the lead_gen phase counts as done.

State definitions
-----------------
- ``open``              — deal created, no required phase complete.
- ``lead_gen_done``     — Lead Gen phase done (latest call compliant).
- ``pre_sales_done``    — Pre-Sales phase done.
- ``verbal_done``       — Verbal phase done.
- ``loa_done``          — LOA phase done (E.ON only).
- ``verified``          — Every required phase done.
- ``rejected``          — Terminal. Reviewer manual override.

The old states (passover_done, closer_done, c_call_done, amendment_done)
are retired. Their data was wiped in Phase 0 of the rebuild; new uploads
never produce them.
"""

from __future__ import annotations

from typing import Iterable, Literal


LifecycleStatus = Literal[
    "open",
    "lead_gen_done",
    "pre_sales_done",
    "verbal_done",
    "loa_done",
    "verified",
    "rejected",
]


# Allowed transitions — `rejected` is the only terminal state.
ALLOWED: dict[str, set[str]] = {
    "open": {"lead_gen_done", "pre_sales_done", "verbal_done", "loa_done", "verified", "rejected"},
    "lead_gen_done": {"pre_sales_done", "verbal_done", "loa_done", "verified", "rejected"},
    "pre_sales_done": {"verbal_done", "loa_done", "verified", "rejected"},
    "verbal_done": {"loa_done", "verified", "rejected"},
    "loa_done": {"verified", "rejected"},
    "verified": {"rejected"},
    "rejected": set(),
}


# Required phases per supplier.
# - E.ON variants: 3 stages — LOA bundled into Verbal segment of closer recording.
# - Everyone else: 3 stages — LOA is paper/DocuSign, never appears in audio.
#
# Case-insensitive supplier matching via ``required_phases``.
_EON_PHASES = ["lead_gen", "pre_sales", "verbal"]
_NON_EON_PHASES = ["lead_gen", "pre_sales", "verbal"]

SUPPLIER_PHASE_MATRIX: dict[str, list[str]] = {
    "E.ON":             _EON_PHASES,
    "E.ON Next":        _EON_PHASES,
    "EON":              _EON_PHASES,
    "EON Next":         _EON_PHASES,
    "E.On Energy Solutions Ltd": _EON_PHASES,
    "British Gas":      _NON_EON_PHASES,
    "British Gas Lite": _NON_EON_PHASES,
    "BG Core":          _NON_EON_PHASES,
    "BGL":              _NON_EON_PHASES,
    "Scottish Power":   _NON_EON_PHASES,
    "EDF Energy":       _NON_EON_PHASES,
    "EDF":              _NON_EON_PHASES,
    "Pozitive":         _NON_EON_PHASES,
    "Pozitive Energy":  _NON_EON_PHASES,
}


def required_phases(supplier: str | None) -> list[str]:
    """Required phases for a given supplier. Case-insensitive.
    Unknown suppliers default to the non-E.ON 3-phase variant.
    """
    if not supplier:
        return list(_NON_EON_PHASES)
    if supplier in SUPPLIER_PHASE_MATRIX:
        return SUPPLIER_PHASE_MATRIX[supplier]
    s_low = supplier.lower()
    for k, v in SUPPLIER_PHASE_MATRIX.items():
        if k.lower() == s_low:
            return v
    return list(_NON_EON_PHASES)


# Map from Call.call_type to the supplier-matrix phase name. Locked to
# the 4 canonical values; no legacy aliases.
_CALL_TYPE_TO_PHASE: dict[str, str] = {
    "lead_gen": "lead_gen",
    "pre_sales": "pre_sales",
    "verbal": "verbal",
    "loa": "loa",
}


def call_type_to_phase(call_type: str | None) -> str | None:
    if not call_type:
        return None
    return _CALL_TYPE_TO_PHASE.get(call_type)


def _phase_done_for(calls: Iterable, phase: str) -> bool:
    """Latest-call-per-phase wins: phase is done iff the LATEST finalised
    call of that phase has ``compliance_status == 'compliant'`` (or the
    legacy ``compliant == True``). Earlier non-compliant calls don't
    block the phase if a later re-record landed compliant.
    """
    candidates = []
    for c in calls:
        if getattr(c, "completed_at", None) is None:
            continue
        ct = getattr(c, "call_type", None)
        if call_type_to_phase(ct) != phase:
            continue
        candidates.append(c)
    if not candidates:
        return False
    # Sort by created_at ASC so the LAST element is the latest. created_at
    # is always set at upload time; fallback to completed_at if missing.
    candidates.sort(
        key=lambda c: (getattr(c, "created_at", None) or getattr(c, "completed_at", None)) or 0
    )
    latest = candidates[-1]
    status = (getattr(latest, "compliance_status", None) or "").lower()
    if status == "compliant":
        return True
    legacy = getattr(latest, "compliant", None)
    if legacy is True:
        return True
    return False


def _completed_phases(calls: Iterable, supplier: str | None) -> set[str]:
    """Latest-wins set of phases done for the given supplier's required
    phase list."""
    out: set[str] = set()
    for phase in required_phases(supplier):
        if _phase_done_for(calls, phase):
            out.add(phase)
    return out


def derive_lifecycle_status(deal, calls: Iterable) -> str:
    """Compute the deal's lifecycle_status from its finalised calls
    under the new "latest call per phase wins" rule.

    Returns the *current* status string — caller persists it.
    ``rejected`` is terminal; never downgrades from it.
    """
    current = (getattr(deal, "lifecycle_status", None) or "open")
    if current == "rejected":
        return "rejected"

    supplier = getattr(deal, "supplier", None)
    required = required_phases(supplier)
    done = _completed_phases(calls, supplier)

    if set(required).issubset(done):
        return "verified"

    # Progressive states — emit the most-advanced phase that's done.
    # Order matters: we walk required list in reverse so verbal_done
    # beats pre_sales_done beats lead_gen_done when multiple are
    # complete.
    for phase in reversed(required):
        if phase in done:
            return f"{phase}_done"

    # Nothing required is done yet. Still might have a `loa` recording
    # for E.ON which counted as part of verbal in the matrix — we don't
    # surface a separate loa_done state because the matrix bundles LOA
    # into verbal for E.ON.
    return "open"
