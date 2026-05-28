"""L7 — Intake upsert helpers.

Two pure functions invoked from ``app.routes.upload_call`` after the L7
metadata envelope has been parsed and validated. Both keep their
behaviour deterministic per session-supplied SQLAlchemy ``Session`` and
do **not** commit — the calling route owns the transaction lifecycle so a
failure later in the upload path rolls the upsert back atomically with
the Call row.

Why this lives in its own module:

* ``payload_schema`` / ``validators`` / ``reconcile`` already deal with
  the wire shape and gate logic; mixing DB writes there would muddy the
  layering tests rely on.
* ``app.routes`` is the only caller, but the functions need to be
  individually unit-testable (``test_intake_writes_supplier_to_deal``,
  ``test_intake_dedupes_customer_by_slug``) without spinning up the full
  HTTP route.

The slugify rule mirrors the one in alembic migration
``f6a7b8c9d0e1_l7_customers_table.py`` exactly so the dedupe key stays
stable across the backfill path and the live upsert path.
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Optional

from sqlalchemy.orm import Session

from app.intake.payload_schema import CustomerMeta, DealMeta
from app.models import Customer, CustomerDeal

log = logging.getLogger("compliance")


def _slugify(legal_name: str, trading_as: Optional[str] = None) -> str:
    """Build the customer dedupe key from ``legal_name`` (+ optional
    ``trading_as``). Matches the migration helper character-for-character
    so the live runtime path produces the same slug the backfill produced."""
    raw = (legal_name or "").strip()
    if trading_as:
        raw = f"{raw} {trading_as.strip()}"
    s = raw.lower()
    s = re.sub(r"[\s/_]+", "-", s)
    s = re.sub(r"[^a-z0-9\-]", "", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "unknown"


def upsert_customer(meta: CustomerMeta, db: Session) -> Customer:
    """Find-or-create a Customer row keyed on slug(legal_name + trading_as).

    Caller must have verified ``meta.legal_name`` is non-empty — we raise
    here if it isn't, because creating a Customer row with a NULL
    legal_name violates the column constraint and we'd rather fail fast
    at the Python boundary than hand a 500 to the upload client.
    """
    if not meta.legal_name:
        raise ValueError("upsert_customer requires customer.legal_name")

    slug = _slugify(meta.legal_name, meta.trading_as)
    existing = db.query(Customer).filter(Customer.slug == slug).first()
    if existing is not None:
        return existing

    customer = Customer(
        id=uuid.uuid4(),
        legal_name=meta.legal_name,
        trading_as=meta.trading_as,
        dob=meta.dob,
        company_number=meta.company_number,
        charity_number=meta.charity_number,
        address_postcode=meta.address_postcode,
        business_type=meta.business_type,
        vulnerable_customer_flag=bool(meta.vulnerable_customer_flag),
        slug=slug,
    )
    db.add(customer)
    db.flush()
    return customer


def upsert_deal(
    meta: DealMeta,
    customer_id: uuid.UUID,
    customer_name: str,
    db: Session,
) -> CustomerDeal:
    """Find-or-create a CustomerDeal scoped to ``customer_id``.

    Two paths:

    * ``meta.existing_deal_id`` is set → look up the row, verify it
      belongs to ``customer_id``, and return it. Mismatch raises
      ``ValueError`` so the route can convert it to a 400.
    * Otherwise → insert a fresh row populating every L7 deal field the
      envelope carries (``supplier``, meter ids, commission, term,
      docusign reference). ``customer_name`` is duplicated onto the row
      for backward-compat with the legacy /api/customers list view that
      groups by it (the migration backfilled but the live writer must
      keep the field consistent).
    """
    if meta.existing_deal_id:
        # Wave-42 (python-reviewer agent a7c6c8ea542082642 MED — write
        # skew): lock the row so two concurrent uploads with different
        # MPANs can't both observe `existing.mpan_electricity is None`
        # and silently discard one writer's value via the backfill below.
        # Idempotent — the same Postgres transaction's earlier
        # SELECT FOR UPDATE in routes.py is the original lock holder.
        existing = (
            db.query(CustomerDeal)
            .filter(CustomerDeal.id == meta.existing_deal_id)
            .with_for_update()
            .first()
        )
        if existing is None:
            raise ValueError(f"deal {meta.existing_deal_id} not found")
        if existing.customer_id is not None and existing.customer_id != customer_id:
            raise ValueError(
                f"deal {meta.existing_deal_id} belongs to a different customer"
            )
        # Backfill customer_id on legacy rows that were created before
        # the L7 customers table existed.
        if existing.customer_id is None:
            existing.customer_id = customer_id

        # Wave-42 (2026-05-28) — backfill MPAN/MPRN onto an existing deal
        # row when the reviewer's upload form supplied a meter but the
        # stored deal had none. Solves owner-reported gap: typing MPAN on
        # the Customer-page upload form succeeded (post wave-41) yet the
        # deal-detail page kept showing "—" because this branch returned
        # the existing row untouched.
        #
        # Conflict cases (existing meter set AND differs from supplied)
        # are NOT overwritten silently — that is surfaced as a
        # non-blocking warning by `validators.existing_deal_consistency`
        # which the route arms with these same fields. Reviewers who
        # genuinely need to change a stored MPAN use the explicit
        # /api/calls/{id}/metadata edit path; the upload form is for
        # adding calls, not for destructive deal mutation.
        if meta.mpan_electricity and not existing.mpan_electricity:
            existing.mpan_electricity = meta.mpan_electricity
            # python-reviewer agent a7c6c8ea542082642 NIT — match the
            # codebase's enrichment-log style (`📅 DATE_EXTRACTOR
            # applied`, `✍️ NAME_PROMOTE_REVERSE`) so the audit trail
            # shows which upload caused the meter to appear on the row.
            log.info(
                "✍️ DEAL_BACKFILL deal_id=%s field=mpan_electricity",
                existing.id,
            )
        if meta.mprn_gas and not existing.mprn_gas:
            existing.mprn_gas = meta.mprn_gas
            log.info(
                "✍️ DEAL_BACKFILL deal_id=%s field=mprn_gas",
                existing.id,
            )
        return existing

    deal = CustomerDeal(
        id=uuid.uuid4(),
        customer_id=customer_id,
        customer_name=customer_name,
        status="in_progress",
        supplier=meta.supplier.value if meta.supplier is not None else None,
        mpan_electricity=meta.mpan_electricity,
        mprn_gas=meta.mprn_gas,
        deal_value_gbp=meta.deal_value_gbp_annual,
        commission_value=meta.commission_value,
        commission_unit=meta.commission_unit,
        expected_live_date=meta.expected_live_date,
        term_months=meta.term_months,
        docusign_reference=meta.docusign_reference,
    )
    db.add(deal)
    db.flush()
    return deal
