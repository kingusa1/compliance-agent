"""Pydantic schema for the L7 structured-intake payload.

The frontend sends a multipart upload with two parts:

  1. ``audio_file`` — the call recording (mp3/wav/m4a/ogg/flac).
  2. ``metadata`` — a JSON envelope shaped like :class:`IntakePayload` below.

If ``metadata`` is omitted, ``app.routes.upload_call`` falls back to the
legacy form-encoded shape (customer_name + call_type + deal_id) for
backwards compatibility — that path is unaffected by L7.

Every field in :class:`CustomerMeta` and :class:`DealMeta` is optional so
the form supports the four documented intake paths:

  * full-auto (dev-mode, audio only)
  * full-manual (every field filled)
  * mixed (most realistic — supplier + customer manual, MPAN/value auto)
  * mismatch (manual disagrees with pipeline → METADATA_MISMATCH flag)

Field-level required-ness is enforced at the validation-gate layer
(``app.intake.validators``) instead of via ``Optional``-vs-required Pydantic
because dev-mode workflows submit blank manual fields and rely on auto
detection. Keeping the schema permissive lets the gate logic decide.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Supplier enum — 13 keys, matches frontend SupplierDropdown exactly.
# E.ON and E.ON Next Energy are DISTINCT keys per gates Step 3 and the
# extraction-pass-2 audit verdict — do NOT collapse them.
# ---------------------------------------------------------------------------


class SupplierEnum(str, Enum):
    EON = "E.ON"  # bundled-LOA model
    EON_NEXT = "E.ON Next Energy"  # standalone-LOA model
    BG_CORE = "British Gas Core"
    BG_LITE = "British Gas Lite"
    BG_BUSINESS = "British Gas Business"
    BG_TRADING = "British Gas Trading"
    POZITIVE = "Pozitive"
    YU_ENERGY = "Yu Energy"
    SMARTEST = "Smartest Energy"
    AFFECT = "Affect Energy"
    BRITANNIA = "Britannia Gas"
    UNITED_GP = "United Gas & Power"
    TOTAL_ENERGIES = "TotalEnergies (out-of-matrix)"
    OTHER = "Other"


# ---------------------------------------------------------------------------
# Per-customer metadata (8 fields).
# ---------------------------------------------------------------------------


class CustomerMeta(BaseModel):
    """Customer-level metadata — top-level entity per the locked decision in
    ``docs/superpowers/specs/2026-04-30-customer-entity-model.md``.

    A customer can have many deals over time. ``legal_name`` is the only
    field with practical required-ness (we cannot create a Customer row
    without it), but for parity with the dev-mode auto path we keep it
    optional here and let the validator gate enforce it conditionally.
    """

    legal_name: Optional[str] = None
    trading_as: Optional[str] = None
    dob: Optional[date] = None
    company_number: Optional[str] = None
    charity_number: Optional[str] = None
    address_postcode: Optional[str] = None
    business_type: Optional[
        Literal["sole_trader", "limited", "partnership", "charity"]
    ] = None
    vulnerable_customer_flag: bool = False


# ---------------------------------------------------------------------------
# Per-deal metadata (9 fields).
# ---------------------------------------------------------------------------


class DealMeta(BaseModel):
    """Deal-level metadata — groups 2-3 calls per supplier matrix.

    ``deal_value_gbp_annual`` is the customer's annual energy cost (NOT the
    broker's commission). The ``commission_value`` + ``commission_unit``
    pair tracks broker commission separately — see digest §6.

    ``existing_deal_id`` distinguishes "+ New deal" (None → create) from
    "attach to existing" (UUID → look up).
    """

    supplier: Optional[SupplierEnum] = None
    mpan_electricity: Optional[str] = None
    mprn_gas: Optional[str] = None
    deal_value_gbp_annual: Optional[Decimal] = None
    commission_value: Optional[Decimal] = None
    commission_unit: Optional[Literal["pct", "gbp"]] = None
    expected_live_date: Optional[date] = None
    term_months: Optional[Literal[12, 24, 36, 48, 60]] = None
    docusign_reference: Optional[str] = None
    existing_deal_id: Optional[UUID] = None

    @field_validator("mpan_electricity", "mprn_gas")
    @classmethod
    def _strip_meter(cls, v: Optional[str]) -> Optional[str]:
        # Tracker entries occasionally include spaces or hyphens; canonical
        # storage is digits only so downstream queries match cleanly.
        if v is None:
            return None
        cleaned = "".join(ch for ch in v if ch.isdigit())
        return cleaned or None


# ---------------------------------------------------------------------------
# Per-call metadata (5 fields).
# ---------------------------------------------------------------------------


class CallMeta(BaseModel):
    """Call-level metadata — required at intake (every upload is a call).

    The 7-enum ``call_type`` covers Watt's full taxonomy:

      * ``lead_gen`` — first contact
      * ``closer`` — verbal sale
      * ``amendment`` — corrective re-sale
      * ``c_call`` — corrective compliance call
      * ``standalone_loa`` — separate LOA call (non-E.ON suppliers)
      * ``passover`` — handover call (digest line 246)
      * ``full`` — single combined recording (digest §2 line 51)
    """

    call_type: Literal[
        "lead_gen",
        "closer",
        "amendment",
        "c_call",
        "standalone_loa",
        "passover",
        "full",
    ]
    sales_agent: Optional[str] = None
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# Top-level envelope.
# ---------------------------------------------------------------------------


class IntakePayload(BaseModel):
    """The full structured-intake envelope, sent as a JSON ``metadata`` part
    in the multipart upload alongside ``audio_file``.

    ``dev_auto_detect`` defaults to ``True`` for the dev workflow; the
    frontend hides the toggle in production (NEXT_PUBLIC_DEV_MODE=false).
    When ``True`` and manual fields are blank, the pipeline fills every
    field via auto-detection and tags ``_source=auto``. When ``False``,
    the validator gate enforces required-ness on the manual side.
    """

    customer: CustomerMeta = Field(default_factory=CustomerMeta)
    deal: DealMeta = Field(default_factory=DealMeta)
    call: CallMeta
    dev_auto_detect: bool = True
    # B-3: customer-page upload pre-fills this with the locked customer's
    # uuid so the upload route attaches to that Customer row directly,
    # skipping the legal_name → upsert lookup. Mutually inclusive with a
    # populated ``customer.legal_name`` (the form sends both for parity).
    customer_id: Optional[UUID] = None


# ---------------------------------------------------------------------------
# Convenience: ordered list of supplier keys for UI rendering.
# Mirrors the frontend SupplierDropdown so the two stay in sync.
# ---------------------------------------------------------------------------


SUPPLIER_DISPLAY_ORDER: List[SupplierEnum] = [
    SupplierEnum.EON,
    SupplierEnum.EON_NEXT,
    SupplierEnum.BG_CORE,
    SupplierEnum.BG_LITE,
    SupplierEnum.BG_BUSINESS,
    SupplierEnum.BG_TRADING,
    SupplierEnum.POZITIVE,
    SupplierEnum.YU_ENERGY,
    SupplierEnum.SMARTEST,
    SupplierEnum.AFFECT,
    SupplierEnum.BRITANNIA,
    SupplierEnum.UNITED_GP,
    SupplierEnum.TOTAL_ENERGIES,
    SupplierEnum.OTHER,
]
