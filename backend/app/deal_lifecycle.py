"""Watt deal lifecycle state machine (Pillar 3 / L3).

Maps the real Watt sales workflow:
    1 customer → 1 deal → up to 5 calls (Lead Gen, Closer, Standalone-LOA,
    Amendment, C-call). Each completed call advances the deal's
    ``lifecycle_status`` along a constrained DAG.

State definitions
-----------------
- ``open``           — deal created, no call has finalized yet.
- ``lead_gen_done``  — Lead Gen call completed (gate 1 passed).
- ``closer_done``    — Closer completed but the supplier still needs a
                      standalone LOA (British Gas / Scottish Power /
                      EDF Energy / Pozitive). The deal is *not* verified
                      until the LOA call lands.
- ``c_call_done``    — Confirmation call ("C-call") finalized after a
                      verified deal — corrective transition. Does not
                      block ``verified`` and does not appear in
                      ``missing_calls``.
- ``amendment_done`` — Amendment call finalized (corrective).
- ``verified``       — All required phases per the supplier matrix are
                      complete. E.ON only needs Lead Gen + Closer
                      (LOA bundled). Other suppliers need the standalone
                      LOA on top.
- ``rejected``       — Terminal. No transitions out; reviewer manual
                      override.

Allowed transitions
-------------------
The ``ALLOWED`` table below is the canonical state machine and is
enforced by ``derive_lifecycle_status``. Last-writer-wins semantics:
the function recomputes the status from the current set of finalized
calls every time a call hits ``finalize`` — so out-of-order uploads
(e.g. Closer arrives before Lead Gen) eventually converge to the right
state once both calls land. We never *downgrade* out of ``rejected``;
that's the only terminal state.

Supplier matrix
---------------
``SUPPLIER_PHASE_MATRIX`` lists the *required* phases per supplier.
Intentionally keyed on ``"E.ON"`` (NOT ``"E.ON Next"``) — gates file
Step 3 is explicit that E.ON Next does not get the bundled-LOA variant.
The detection layer canonicalizes the supplier name upstream.

C-call is *not* in any supplier's required list — it's a corrective
transition that doesn't block ``verified`` and doesn't appear in
``missing_calls``.
"""

from __future__ import annotations

from typing import Iterable, Literal


LifecycleStatus = Literal[
    "open",
    "lead_gen_done",
    "passover_done",
    "closer_done",
    "c_call_done",
    "amendment_done",
    "verified",
    "rejected",
]


# Allowed transitions per design_decisions.lifecycle_state_machine.
# `rejected` is terminal — no outgoing edges.
ALLOWED: dict[str, set[str]] = {
    "open": {"lead_gen_done", "rejected"},
    "lead_gen_done": {"passover_done", "closer_done", "rejected"},
    "passover_done": {"closer_done", "rejected"},
    "closer_done": {"verified", "amendment_done", "c_call_done", "rejected"},
    "verified": {"c_call_done", "amendment_done", "rejected"},
    "amendment_done": {"verified", "c_call_done", "rejected"},
    "c_call_done": {"verified", "amendment_done", "rejected"},
    "rejected": set(),
}


# Required phases per supplier — CORRECT per user 2026-05-11:
#   - E.ON / E.ON Next: 3 stages (Lead Gen → Passover → Closer, LOA bundled)
#   - All other suppliers: 4 stages (+ Standalone LOA)
# Plus optional `amendment` and `c_call` corrective phases for any deal.
#
# "Passover" is the warm-handover between the lead-gen agent and the closer.
# It's a distinct file in every Watt customer audio folder and a distinct
# segmentation stage in the Watt AI Compliance Tech Spec (TS §3).
# Previously the matrix only had Lead Gen + Closer (+ LOA) — Passover was
# missing, which is why deals reached "verified" prematurely.
SUPPLIER_PHASE_MATRIX: dict[str, list[str]] = {
    "E.ON":             ["lead_gen", "passover", "closer"],
    "E.ON Next":        ["lead_gen", "passover", "closer"],
    "EON":              ["lead_gen", "passover", "closer"],
    "EON Next":         ["lead_gen", "passover", "closer"],
    "British Gas":      ["lead_gen", "passover", "closer", "standalone_loa"],
    "British Gas Lite": ["lead_gen", "passover", "closer", "standalone_loa"],
    "BG Core":          ["lead_gen", "passover", "closer", "standalone_loa"],
    "BGL":              ["lead_gen", "passover", "closer", "standalone_loa"],
    "Scottish Power":   ["lead_gen", "passover", "closer", "standalone_loa"],
    "EDF Energy":       ["lead_gen", "passover", "closer", "standalone_loa"],
    "EDF":              ["lead_gen", "passover", "closer", "standalone_loa"],
    "Pozitive":         ["lead_gen", "passover", "closer", "standalone_loa"],
    "Pozitive Energy":  ["lead_gen", "passover", "closer", "standalone_loa"],
}


def required_phases(supplier: str | None) -> list[str]:
    """Required phases for a given supplier. Unknown suppliers default
    to the full standalone-LOA variant (safer for compliance review).

    Case-insensitive match so DB drift between "E.ON next" / "E.ON Next"
    doesn't trigger the default 3-phase rule for bundled suppliers.
    """
    if not supplier:
        return ["lead_gen", "closer", "standalone_loa"]
    # Direct hit first.
    if supplier in SUPPLIER_PHASE_MATRIX:
        return SUPPLIER_PHASE_MATRIX[supplier]
    # Case-insensitive fallback.
    s_low = supplier.lower()
    for k, v in SUPPLIER_PHASE_MATRIX.items():
        if k.lower() == s_low:
            return v
    return ["lead_gen", "closer", "standalone_loa"]


# Map from Call.call_type to the supplier-matrix phase name.
# 'passover' is the warm-handover between lead-gen and closer agents
# (TS §3 segmentation stage). 'loa' aliases to 'standalone_loa'.
_CALL_TYPE_TO_PHASE: dict[str, str] = {
    "lead_gen": "lead_gen",
    "passover": "passover",
    "closer": "closer",
    "verbal": "closer",
    "standalone_loa": "standalone_loa",
    "loa": "standalone_loa",
    "amendment": "amendment",
    "c_call": "c_call",
}


def call_type_to_phase(call_type: str | None) -> str | None:
    if not call_type:
        return None
    return _CALL_TYPE_TO_PHASE.get(call_type)


def _completed_phases(calls: Iterable) -> set[str]:
    """Collect the set of supplier-matrix phases for which *some* call
    has finalized. We treat any call with ``completed_at`` set as
    finalized — the caller is responsible for filtering to the right
    deal.

    Special case: a ``call_type == "full"`` recording captures the whole
    deal in one go (typical for E.ON's bundled flow), so it covers BOTH
    ``lead_gen`` and ``closer`` for lifecycle purposes. Without this, a
    single full-call deal would never leave the ``open`` state because
    "full" doesn't map to any phase. (audit-late B6.)
    """
    out: set[str] = set()
    for c in calls:
        if getattr(c, "completed_at", None) is None:
            continue
        ct = getattr(c, "call_type", None)
        if ct == "full":
            # A "full" recording captures the entire E.ON-style bundled
            # flow on one call: lead-gen intro + passover/handover + closer
            # (LOA bundled). Credit all three phases.
            out.add("lead_gen")
            out.add("passover")
            out.add("closer")
            continue
        phase = call_type_to_phase(ct)
        if phase:
            out.add(phase)
    return out


def derive_lifecycle_status(deal, calls: Iterable) -> str:
    """Compute the deal's lifecycle_status from its finalized calls.

    Last-writer-wins: ``finalize`` calls this every time a call
    completes, so transient out-of-order states converge once all the
    calls land. Returns the *current* status string — the caller is
    responsible for persisting it.

    Rejected is terminal: if the deal is already rejected, we keep it
    rejected regardless of further call activity.
    """
    current = (getattr(deal, "lifecycle_status", None) or "open")
    if current == "rejected":
        return "rejected"

    completed = _completed_phases(calls)
    required = set(required_phases(getattr(deal, "supplier", None)))

    has_lead_gen = "lead_gen" in completed
    has_passover = "passover" in completed
    has_closer = "closer" in completed
    has_loa = "standalone_loa" in completed
    has_amendment = "amendment" in completed
    has_c_call = "c_call" in completed

    # Verified: every required phase finalized.
    if required.issubset(completed):
        if has_c_call:
            return "c_call_done"
        if has_amendment:
            return "amendment_done"
        return "verified"

    # Corrective transitions take precedence over partial-progress
    # states once Closer has happened.
    if has_closer:
        if has_c_call:
            return "c_call_done"
        if has_amendment:
            return "amendment_done"
        # Closer landed but a required follow-up (LOA) is still
        # missing → closer_done.
        return "closer_done"

    if has_passover:
        return "passover_done"

    if has_lead_gen:
        return "lead_gen_done"

    # No required phase finalized yet. Note: a c_call or amendment
    # arriving before lead_gen is technically out of order — we keep
    # the deal at `open` until the proper sequence catches up. This
    # matches last-writer-wins convergence.
    return "open"
