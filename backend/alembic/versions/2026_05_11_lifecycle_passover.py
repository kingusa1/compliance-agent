"""Add 'passover_done' to ck_customer_deals_lifecycle_status.

The supplier workflow matrix was updated to include the Passover phase
(warm handover between lead-gen and closer). E.ON now needs 3 stages
(Lead Gen + Passover + Closer) and every other supplier needs 4
(+ Standalone LOA). The lifecycle resolver returns 'passover_done'
when only that phase has finalised — the existing CHECK constraint
must accept the new value.

Revision ID: 20260511_passover
Revises: 20260510_cascade
Create Date: 2026-05-11
"""
from alembic import op


revision = "20260511_passover"
down_revision = "20260510_cascade"
branch_labels = None
depends_on = None


_NEW_STATES = (
    "open",
    "lead_gen_done",
    "passover_done",
    "closer_done",
    "c_call_done",
    "amendment_done",
    "verified",
    "rejected",
)


def upgrade() -> None:
    op.drop_constraint(
        "ck_customer_deals_lifecycle_status",
        "customer_deals",
        type_="check",
    )
    in_list = ",".join(f"'{s}'" for s in _NEW_STATES)
    op.create_check_constraint(
        "ck_customer_deals_lifecycle_status",
        "customer_deals",
        f"lifecycle_status IN ({in_list})",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_customer_deals_lifecycle_status",
        "customer_deals",
        type_="check",
    )
    op.create_check_constraint(
        "ck_customer_deals_lifecycle_status",
        "customer_deals",
        "lifecycle_status IN ('open','lead_gen_done','closer_done',"
        "'c_call_done','amendment_done','verified','rejected')",
    )
