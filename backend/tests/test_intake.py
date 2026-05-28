"""L7 — Structured intake tests.

Seven tests cover the four documented intake paths plus three validation
gates plus supplier canonicalization (the supplier-phase gate was retired
in the 2026-05-12 taxonomy rebuild):

  1. test_full_auto_path           — dev mode, audio only, all blank
  2. test_full_manual_path         — every field typed
  3. test_mixed_path               — partial manual, partial auto
  4. test_mismatch_path            — manual + auto disagree → flag
  5. test_validation_at_least_one_meter — 422 when both meters blank
  6. test_validation_charity_consistency — warning when charity_number
      missing
  7. test_supplier_canonicalization     — alias maps preserve E.ON-vs-
      E.ON-Next distinction

Tests run as pure-function unit tests against the schema, reconciler,
validators, and canonicalizer — they don't require the Customer ORM
table (owned by the main session migration). The route-integration tests
that need a live DB will be added once the migration lands.
"""

from __future__ import annotations

import uuid

import pytest

from app.intake.payload_schema import (
    CallMeta,
    CustomerMeta,
    DealMeta,
    IntakePayload,
    SupplierEnum,
)
# Pre-import models at module scope so SQLAlchemy registers the
# Customer / CustomerDeal tables on Base.metadata BEFORE the test_db
# fixture calls create_all. Without this, the first test using the
# fixture trips "no such table: customers" because the module is
# imported lazily inside the test body and the fixture has already run.
from app import models as _models  # noqa: F401
from app.intake.reconcile import (
    METADATA_MISMATCH_RULE_ID,
    find_mismatches,
    reconcile_metadata,
)
from app.intake.supplier_canonical import canonicalize
from app.intake.validators import (
    ValidationGateError,
    ValidationWarning,
    validate_payload,
)


# ---------------------------------------------------------------------------
# 1. Full-auto path — dev mode, audio only, every field blank.
# ---------------------------------------------------------------------------


def test_full_auto_path():
    """Dev workflow: reviewer drops audio with no metadata. Validators must
    let it through (no meter required) because auto-detect will fill the
    fields. Reconciler returns ``source=auto`` for everything the pipeline
    discovers."""
    payload = IntakePayload(
        customer=CustomerMeta(),
        deal=DealMeta(),
        call=CallMeta(call_type="lead_gen"),
        dev_auto_detect=True,
    )
    # Blocking gate must NOT raise — dev_auto_detect bypasses the
    # at-least-one-meter requirement.
    warnings = validate_payload(payload)
    assert warnings == []

    # Simulate the pipeline filling every field after upload.
    auto = {
        "supplier": "E.ON Next Energy",
        "mpan_electricity": "1234567890123",
        "deal_value_gbp_annual": 1390.00,
    }
    reconciled = reconcile_metadata(manual={}, auto=auto)
    assert reconciled["supplier"].source == "auto"
    assert reconciled["mpan_electricity"].source == "auto"
    assert reconciled["deal_value_gbp_annual"].source == "auto"


# ---------------------------------------------------------------------------
# 2. Full-manual path — reviewer types every field.
# ---------------------------------------------------------------------------


def test_full_manual_path():
    """Production workflow: reviewer types everything, dev_auto_detect=False.
    Reconciler stamps ``source=manual`` when auto comes back blank."""
    payload = IntakePayload(
        customer=CustomerMeta(legal_name="Acme Ltd", business_type="limited"),
        deal=DealMeta(
            supplier=SupplierEnum.BG_CORE,
            mpan_electricity="1234567890123",
            mprn_gas="9876543210",
            deal_value_gbp_annual="1390.00",
        ),
        call=CallMeta(call_type="verbal", sales_agent="Sarah Ali"),
        dev_auto_detect=False,
    )
    warnings = validate_payload(payload)
    # No charity, no E.ON+standalone, no existing deal → no warnings.
    assert warnings == []

    manual = {
        "supplier": "British Gas Core",
        "mpan_electricity": "1234567890123",
        "mprn_gas": "9876543210",
        "deal_value_gbp_annual": "1390.00",
        "legal_name": "Acme Ltd",
    }
    auto: dict = {}  # Pipeline didn't add anything new.
    reconciled = reconcile_metadata(manual=manual, auto=auto)
    for v in reconciled.values():
        assert v.source == "manual"


# ---------------------------------------------------------------------------
# 3. Mixed path — most realistic. Reviewer fills supplier+customer,
#    pipeline fills MPAN+value.
# ---------------------------------------------------------------------------


def test_mixed_path():
    """Realistic workflow: reviewer types supplier + customer, pipeline
    fills MPAN + deal_value. Reconciler tags each field with its source."""
    manual = {
        "legal_name": "Acme Ltd",
        "supplier": "E.ON Next Energy",
    }
    auto = {
        "mpan_electricity": "1234567890123",
        "deal_value_gbp_annual": 1390.00,
    }
    reconciled = reconcile_metadata(manual=manual, auto=auto)
    assert reconciled["legal_name"].source == "manual"
    assert reconciled["supplier"].source == "manual"
    assert reconciled["mpan_electricity"].source == "auto"
    assert reconciled["deal_value_gbp_annual"].source == "auto"


# ---------------------------------------------------------------------------
# 4. Mismatch path — manual='E.ON', auto='British Gas' → METADATA_MISMATCH.
# ---------------------------------------------------------------------------


def test_mismatch_path():
    """When manual and auto both fill the same field with different
    values, reconciler tags ``source=mismatch`` and the call site is
    expected to emit a Flag with rule_id=METADATA_MISMATCH severity=high.
    Manual remains the persisted value (ground truth)."""
    manual = {"supplier": "E.ON"}
    auto = {"supplier": "British Gas Core"}
    reconciled = reconcile_metadata(manual=manual, auto=auto)
    assert reconciled["supplier"].source == "mismatch"
    # Manual wins — it's the "ground truth" persisted value.
    assert reconciled["supplier"].value == "E.ON"

    # The mismatch finder should produce flag-ready evidence rows.
    rows = find_mismatches(reconciled, manual, auto)
    assert len(rows) == 1
    assert rows[0]["field"] == "supplier"
    assert rows[0]["manual"] == "E.ON"
    assert rows[0]["auto"] == "British Gas Core"
    assert "Manual:" in rows[0]["evidence"]
    assert "Auto:" in rows[0]["evidence"]
    # Sanity-check the rule id constant is what callers will write.
    assert METADATA_MISMATCH_RULE_ID == "METADATA_MISMATCH"


# ---------------------------------------------------------------------------
# 5. Validation gate — at least one meter required (BLOCKING).
# ---------------------------------------------------------------------------


def test_validation_at_least_one_meter():
    """When dev_auto_detect=False AND both MPAN and MPRN are blank, the
    gate must raise ValidationGateError (route translates → 422)."""
    payload = IntakePayload(
        customer=CustomerMeta(legal_name="Acme Ltd"),
        deal=DealMeta(supplier=SupplierEnum.BG_CORE),
        call=CallMeta(call_type="verbal"),
        dev_auto_detect=False,
    )
    with pytest.raises(ValidationGateError) as exc_info:
        validate_payload(payload)
    assert exc_info.value.code == "meter_required"


def test_validation_meters_array_flattens_to_mpan_electricity():
    """Wave-41 — owner-reported: form sent `deal.meters: [{mpan, mprn}]`
    but the gate read `mpan_electricity` / `mprn_gas` flat fields. The
    new model_validator must flatten the first non-empty meter row so
    the gate passes when meters[] is the only signal."""
    payload = IntakePayload(
        customer=CustomerMeta(legal_name="Marsden Capital Ltd"),
        deal=DealMeta.model_validate({
            "supplier": SupplierEnum.BG_CORE,
            # Form-shaped meters array — no flat fields populated.
            "meters": [{"mpan": "1012371240692", "mprn": ""}],
        }),
        call=CallMeta(call_type="verbal"),
        dev_auto_detect=False,
    )
    # Flatten happened — gate now sees the meter.
    assert payload.deal.mpan_electricity == "1012371240692"
    # Gate is satisfied (does not raise).
    validate_payload(payload)


def test_validation_meters_array_preserves_explicit_flat_fields():
    """If both the array and the flat field are present, the flat field
    is authoritative (no overwrite)."""
    payload_deal = DealMeta.model_validate({
        "supplier": SupplierEnum.BG_CORE,
        "mpan_electricity": "9999999999999",
        "meters": [{"mpan": "1012371240692"}],
    })
    # Flat field wins.
    assert payload_deal.mpan_electricity == "9999999999999"


def test_validation_meters_array_dual_fuel():
    """Each meter kind picked independently from the first row that
    carries it — supports the dual-fuel form pattern."""
    deal = DealMeta.model_validate({
        "supplier": SupplierEnum.BG_CORE,
        "meters": [
            {"mpan": "1012371240692"},
            {"mprn": "8765432109876"},
        ],
    })
    assert deal.mpan_electricity == "1012371240692"
    assert deal.mprn_gas == "8765432109876"


def test_validation_meters_array_strips_spaces_and_hyphens():
    """Wave-41 — values flattened from `meters[]` must go through the
    same digits-only normaliser the `_strip_meter` field_validator
    applies to the flat fields. Otherwise the canonical storage
    diverges depending on which form-shape the reviewer used."""
    deal = DealMeta.model_validate({
        "supplier": SupplierEnum.BG_CORE,
        "meters": [
            {"mpan": "1012 371-240 692", "mprn": "8765/4321/09876"},
        ],
    })
    assert deal.mpan_electricity == "1012371240692"
    assert deal.mprn_gas == "8765432109876"


# ---------------------------------------------------------------------------
# 6. Validation gate — charity consistency (WARNING).
# ---------------------------------------------------------------------------


def test_validation_charity_consistency():
    """business_type='charity' + blank charity_number → warning, but
    submission is still allowed (warning, not blocking)."""
    payload = IntakePayload(
        customer=CustomerMeta(
            legal_name="Charity Test",
            business_type="charity",
            charity_number=None,
        ),
        deal=DealMeta(
            supplier=SupplierEnum.BG_CORE,
            mpan_electricity="1234567890123",
        ),
        call=CallMeta(call_type="verbal"),
        dev_auto_detect=False,
    )
    warnings = validate_payload(payload)
    codes = [w.code for w in warnings]
    assert "charity_number_recommended" in codes
    # Sanity-check shape — frontend reads .field to highlight the input.
    charity_warn = next(w for w in warnings if w.code == "charity_number_recommended")
    assert isinstance(charity_warn, ValidationWarning)
    assert charity_warn.field == "customer.charity_number"


# ---------------------------------------------------------------------------
# 7. (Removed 2026-05-12 taxonomy rebuild) — the supplier_phase_match
#    validator was retired with the old standalone_loa call_type.
#    The classifier + non-E.ON LOA drop now enforces this guarantee
#    inside ``app.agents.content_classifier``.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 8. Supplier canonicalization — E.ON Next Energy stays distinct from E.ON.
# ---------------------------------------------------------------------------


def test_supplier_canonicalization():
    """Alias maps must preserve the E.ON-vs-E.ON-Next distinction. Per
    gates Step 3 and the extraction-pass-2 audit verdict, these are
    distinct keys with different LOA models — collapsing them breaks
    the supplier_phase_match gate at L3."""
    # The exact case from the spec: 'E.On Next Energy Ltd' must NOT
    # collapse to 'E.ON' — it must hit 'E.ON Next Energy'.
    assert canonicalize("E.On Next Energy Ltd") == "E.ON Next Energy"
    assert canonicalize("eon next") == "E.ON Next Energy"
    assert canonicalize("E.ON Next") == "E.ON Next Energy"

    # Bare E.ON variants still canonicalize to E.ON (not E.ON Next).
    assert canonicalize("E.ON") == "E.ON"
    assert canonicalize("eon") == "E.ON"
    assert canonicalize("E.ON Energy Solutions Ltd") == "E.ON"

    # British Gas family: four distinct keys, never collapsed.
    assert canonicalize("BG Core") == "British Gas Core"
    assert canonicalize("BG Lite") == "British Gas Lite"
    assert canonicalize("BG Business") == "British Gas Business"
    assert canonicalize("British Gas Trading Ltd") == "British Gas Trading"

    # Out-of-matrix and unknown.
    assert canonicalize("TotalEnergies Gas & Power Ltd") == "TotalEnergies (out-of-matrix)"
    assert canonicalize("Unknown Supplier Ltd") == "Other"
    assert canonicalize("") == "Other"
    assert canonicalize(None) == "Other"


# ---------------------------------------------------------------------------
# 9. Intake upsert — supplier persisted on the deal row (B-1 fix).
# ---------------------------------------------------------------------------


def test_intake_writes_supplier_to_deal(test_db):
    """Reproduces the real Watt audio symptom: L7 envelope with
    deal.supplier='E.ON Next Energy' must land on customer_deals.supplier,
    not stay NULL. Also verifies customer_id linkage is set."""
    from app.intake.upsert import upsert_customer, upsert_deal
    from app.models import Customer, CustomerDeal

    customer_meta = CustomerMeta(legal_name="E.ON Customer Ltd")
    deal_meta = DealMeta(
        supplier=SupplierEnum.EON_NEXT,
        mpan_electricity="1234567890123",
    )

    customer = upsert_customer(customer_meta, test_db)
    deal = upsert_deal(
        deal_meta, customer_id=customer.id, customer_name=customer.legal_name, db=test_db
    )
    test_db.commit()

    fetched = test_db.query(CustomerDeal).filter_by(id=deal.id).one()
    assert fetched.supplier == "E.ON Next Energy"
    assert fetched.customer_id == customer.id
    assert fetched.mpan_electricity == "1234567890123"
    # Customer row was created and linked.
    assert test_db.query(Customer).count() == 1


# ---------------------------------------------------------------------------
# 10. Intake upsert — second call with same legal_name reuses Customer row.
# ---------------------------------------------------------------------------


def test_intake_dedupes_customer_by_slug(test_db):
    """Two intake envelopes with the same legal_name must produce ONE
    customer row (slug-keyed dedupe), not two. The two deals each get
    their own row but share customer_id."""
    from app.intake.upsert import upsert_customer, upsert_deal
    from app.models import Customer, CustomerDeal

    meta = CustomerMeta(legal_name="Acme Holdings Ltd")

    c1 = upsert_customer(meta, test_db)
    deal1 = upsert_deal(
        DealMeta(supplier=SupplierEnum.BG_CORE),
        customer_id=c1.id,
        customer_name=c1.legal_name,
        db=test_db,
    )
    test_db.commit()

    c2 = upsert_customer(meta, test_db)
    deal2 = upsert_deal(
        DealMeta(supplier=SupplierEnum.EON),
        customer_id=c2.id,
        customer_name=c2.legal_name,
        db=test_db,
    )
    test_db.commit()

    assert c1.id == c2.id  # same row, not a duplicate
    assert test_db.query(Customer).count() == 1
    # But two distinct deals, both linked to the one customer.
    assert test_db.query(CustomerDeal).count() == 2
    assert deal1.customer_id == c1.id
    assert deal2.customer_id == c1.id


# ---------------------------------------------------------------------------
# 11. Wave-42 — backfill MPAN/MPRN onto existing deal when row was blank.
# ---------------------------------------------------------------------------


def test_wave42_backfills_mpan_when_existing_deal_blank(test_db):
    """Reviewer fills MPAN on the Customer-page upload form. The deal
    already exists but had no MPAN recorded (pipeline failed to detect
    it, or the deal was created from a tracker spreadsheet). The new
    MPAN must be written through onto the existing row — otherwise the
    deal-detail page keeps showing '—' even though the reviewer just
    typed a value."""
    from app.intake.upsert import upsert_customer, upsert_deal
    from app.models import CustomerDeal

    customer_meta = CustomerMeta(legal_name="Backfill Co")
    customer = upsert_customer(customer_meta, test_db)

    # Seed an existing deal with NO meter recorded.
    deal_meta_initial = DealMeta(supplier=SupplierEnum.BG_CORE)
    existing = upsert_deal(
        deal_meta_initial,
        customer_id=customer.id,
        customer_name=customer.legal_name,
        db=test_db,
    )
    test_db.commit()
    assert existing.mpan_electricity is None
    assert existing.mprn_gas is None

    # Second upload — attach to existing deal, supply MPAN.
    deal_meta_followup = DealMeta(
        existing_deal_id=existing.id,
        mpan_electricity="1012371240692",
        mprn_gas="9876543210",
    )
    resolved = upsert_deal(
        deal_meta_followup,
        customer_id=customer.id,
        customer_name=customer.legal_name,
        db=test_db,
    )
    test_db.commit()

    # Same row returned (not a new insert), and backfilled.
    assert resolved.id == existing.id
    fetched = test_db.query(CustomerDeal).filter_by(id=existing.id).one()
    assert fetched.mpan_electricity == "1012371240692"
    assert fetched.mprn_gas == "9876543210"


def test_wave42_does_not_overwrite_existing_meter(test_db):
    """If the deal already has an MPAN and the reviewer types a
    DIFFERENT MPAN on the upload form, the upsert must preserve the
    stored value. The route surfaces the disagreement to the reviewer
    via existing_deal_consistency warning so they know their input
    wasn't applied (see route-level integration test below)."""
    from app.intake.upsert import upsert_customer, upsert_deal
    from app.models import CustomerDeal

    customer = upsert_customer(CustomerMeta(legal_name="NoOverwrite Co"), test_db)
    existing = upsert_deal(
        DealMeta(
            supplier=SupplierEnum.BG_CORE,
            mpan_electricity="1111111111111",
        ),
        customer_id=customer.id,
        customer_name=customer.legal_name,
        db=test_db,
    )
    test_db.commit()

    # Form supplies a DIFFERENT MPAN.
    resolved = upsert_deal(
        DealMeta(
            existing_deal_id=existing.id,
            mpan_electricity="2222222222222",
        ),
        customer_id=customer.id,
        customer_name=customer.legal_name,
        db=test_db,
    )
    test_db.commit()

    fetched = test_db.query(CustomerDeal).filter_by(id=existing.id).one()
    # Stored value preserved — destructive mutation is reserved for the
    # explicit /api/calls/{id}/metadata edit path.
    assert fetched.mpan_electricity == "1111111111111"
    assert resolved.id == existing.id


def test_wave42_existing_deal_conflict_emits_warning(test_db):
    """Validators.existing_deal_consistency fires `existing_deal_field_conflict`
    when the upload form's MPAN disagrees with the stored deal MPAN.
    Wave-42 (routes.py) wires existing_deal_fields into validate_payload
    so this warning actually reaches the response — before wave-42 the
    machinery existed but was never armed for customer-page uploads."""
    from app.intake import validate_payload

    payload = IntakePayload(
        customer=CustomerMeta(legal_name="Conflict Co"),
        deal=DealMeta(
            existing_deal_id=uuid.uuid4(),
            supplier=SupplierEnum.BG_CORE,
            mpan_electricity="2222222222222",
        ),
        call=CallMeta(call_type="lead_gen"),
        dev_auto_detect=False,
    )
    existing_fields = {
        "supplier": "British Gas Core",
        "mpan_electricity": "1111111111111",
        "mprn_gas": None,
    }
    warnings = validate_payload(payload, existing_fields)
    codes = [w.code for w in warnings]
    assert "existing_deal_field_conflict" in codes
    fields = [w.field for w in warnings if w.code == "existing_deal_field_conflict"]
    assert "deal.mpan_electricity" in fields
    # Wave-42 PII hygiene — the warning message MUST NOT echo the stored
    # MPAN value (security-reviewer agent a66367b9e0631bbc5 MED). The
    # supplied value also stays out of the message; the reviewer knows
    # what they typed, and the deal page is the canonical source for
    # the stored value.
    mpan_warnings = [
        w for w in warnings
        if w.code == "existing_deal_field_conflict"
        and w.field == "deal.mpan_electricity"
    ]
    for w in mpan_warnings:
        assert "1111111111111" not in w.message
        assert "2222222222222" not in w.message
