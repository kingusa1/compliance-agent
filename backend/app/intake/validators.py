"""Intake-time validation gates for L7 structured-intake payloads.

Four gates per the L7 contract:

  1. ``at_least_one_meter`` (BLOCKING — 422):
     ``mpan_electricity`` OR ``mprn_gas`` must be provided.
  2. ``charity_consistency`` (WARNING):
     If ``business_type='charity'`` and ``charity_number`` is blank,
     warn but allow submission.
  3. ``supplier_phase_match`` (WARNING):
     If ``call_type='standalone_loa'`` and ``supplier='E.ON'``, warn —
     E.ON bundles LOA inside the closer, so a standalone_loa call
     against E.ON likely indicates the wrong supplier was picked.
  4. ``existing_deal_consistency`` (WARNING):
     If ``existing_deal_id`` is provided, downstream code is expected to
     compare the new deal fields against the existing row. We surface a
     hook here so the route can short-circuit warning emission when the
     reviewer overrode mismatched fields intentionally.

The blocking gate raises :class:`ValidationGateError`; warning gates
return :class:`ValidationWarning` rows so the route can include them in
the response body for the UI to surface inline.

Dev-mode bypass: when ``dev_auto_detect=True`` AND every meter field is
blank, the at-least-one-meter gate is skipped — the pipeline will fill
the meter fields itself, and blocking the upload would defeat the
auto-detect workflow entirely.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from app.intake.payload_schema import IntakePayload, SupplierEnum


class ValidationGateError(Exception):
    """Blocking validation gate — should produce a 422 from the route.

    ``code`` is a machine-readable identifier so the frontend can show a
    targeted error next to the right form field instead of just printing
    ``str(exc)``.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ValidationWarning:
    """Non-blocking warning. The route includes the list in the response
    so the frontend can render banner-style notes alongside the call row.
    """

    code: str
    message: str
    field: str | None = None


def at_least_one_meter(payload: IntakePayload) -> None:
    """BLOCKING — at least one of mpan_electricity / mprn_gas required.

    Skipped in dev-mode auto-detect path: when the reviewer leaves both
    fields blank AND opts into auto-detect, the pipeline fills meters
    from the transcript. Blocking here would break that workflow.
    """
    deal = payload.deal
    has_mpan = bool(deal.mpan_electricity)
    has_mprn = bool(deal.mprn_gas)
    if has_mpan or has_mprn:
        return
    if payload.dev_auto_detect:
        # Auto-detect will fill meters from the transcript; let it through.
        return
    raise ValidationGateError(
        code="meter_required",
        message="Provide MPAN (electricity) and/or MPRN (gas)",
    )


def charity_consistency(payload: IntakePayload) -> List[ValidationWarning]:
    """Warn (don't block) when business_type='charity' but charity_number
    is missing — Watt's LOA template requires charity_number for charity
    customers, but the reviewer can fill it on the next pass.
    """
    cust = payload.customer
    if cust.business_type == "charity" and not cust.charity_number:
        return [
            ValidationWarning(
                code="charity_number_recommended",
                message="Charity customers require charity_number per LOA",
                field="customer.charity_number",
            )
        ]
    return []


def supplier_phase_match(payload: IntakePayload) -> List[ValidationWarning]:
    """Warn when call_type='standalone_loa' is paired with E.ON.

    E.ON bundles the LOA inside the Closer call (see digest §1 + L3
    supplier_phase_matrix); if the reviewer marks a call as standalone_loa
    against E.ON, they probably meant E.ON Next Energy (which IS a
    standalone-LOA supplier) or another supplier. Warning, not blocking.
    """
    deal = payload.deal
    call = payload.call
    if call.call_type == "standalone_loa" and deal.supplier == SupplierEnum.EON:
        return [
            ValidationWarning(
                code="supplier_phase_mismatch",
                message=(
                    "E.ON bundles LOA in Closer; standalone_loa not expected "
                    "for this supplier (did you mean E.ON Next Energy?)"
                ),
                field="deal.supplier",
            )
        ]
    return []


def existing_deal_consistency(
    payload: IntakePayload,
    existing_deal_fields: dict | None = None,
) -> List[ValidationWarning]:
    """Warn when an existing-deal attachment has fields that disagree
    with the existing row. ``existing_deal_fields`` is supplied by the
    route after looking up ``deal.existing_deal_id`` in the DB; pass
    ``None`` to skip this gate (e.g. in unit tests).
    """
    if not payload.deal.existing_deal_id or not existing_deal_fields:
        return []
    warnings: List[ValidationWarning] = []
    new = payload.deal.model_dump(exclude_none=True)
    for field, existing_val in existing_deal_fields.items():
        new_val = new.get(field)
        if new_val is None or existing_val is None:
            continue
        if str(new_val).strip().lower() != str(existing_val).strip().lower():
            warnings.append(
                ValidationWarning(
                    code="existing_deal_field_conflict",
                    message=(
                        f"{field} '{new_val}' conflicts with existing deal "
                        f"value '{existing_val}'"
                    ),
                    field=f"deal.{field}",
                )
            )
    return warnings


def validate_payload(
    payload: IntakePayload,
    existing_deal_fields: dict | None = None,
) -> List[ValidationWarning]:
    """Run all four gates. Raises :class:`ValidationGateError` on the
    blocking gate; returns the union of warnings from the three
    warning-only gates so the route can pass them back in the response.
    """
    at_least_one_meter(payload)
    warnings: List[ValidationWarning] = []
    warnings.extend(charity_consistency(payload))
    warnings.extend(supplier_phase_match(payload))
    warnings.extend(existing_deal_consistency(payload, existing_deal_fields))
    return warnings
