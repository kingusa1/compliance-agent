"""L7 — Structured intake package.

Owns the Watt-aligned upload payload schema, manual-vs-auto reconciliation,
intake-time validation gates, and supplier-name canonicalization for
free-text inputs.

Customer table + ORM extensions + alembic migration are owned by the main
session, not this package — we only consume the resulting `Customer`,
`CustomerDeal`, and `Call` ORM rows. See
`docs/superpowers/specs/2026-04-30-l7-structured-intake-design.md`.
"""

from app.intake.payload_schema import (
    CallMeta,
    CustomerMeta,
    DealMeta,
    IntakePayload,
    SupplierEnum,
)
from app.intake.reconcile import (
    METADATA_MISMATCH_RULE_ID,
    ReconciledField,
    reconcile_metadata,
)
from app.intake.supplier_canonical import (
    SUPPLIER_ALIASES,
    SUPPLIER_KEYS,
    canonicalize,
)
from app.intake.validators import (
    ValidationGateError,
    ValidationWarning,
    validate_payload,
)

__all__ = [
    "CallMeta",
    "CustomerMeta",
    "DealMeta",
    "IntakePayload",
    "SupplierEnum",
    "METADATA_MISMATCH_RULE_ID",
    "ReconciledField",
    "reconcile_metadata",
    "SUPPLIER_ALIASES",
    "SUPPLIER_KEYS",
    "canonicalize",
    "ValidationGateError",
    "ValidationWarning",
    "validate_payload",
]
