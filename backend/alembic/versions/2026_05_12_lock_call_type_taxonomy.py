"""Lock call_type taxonomy to 4 values (lead_gen, pre_sales, verbal, loa)

2026-05-12 taxonomy rebuild. The old vocabulary (passover, closer,
standalone_loa, c_call, amendment, full, verbal-as-old-alias) is gone.

This migration:
- Extends ``ck_customer_deals_lifecycle_status`` to allow the new
  states (pre_sales_done, verbal_done, loa_done) AND keeps the old
  ones temporarily for back-compat during the Phase 0 wipe rollout.
  Once the wipe is run, old states won't appear in fresh data.

Revision ID: 4f9c1d27_locktax
Revises: d5ac554dce56
Create Date: 2026-05-12
"""
from __future__ import annotations

from alembic import op


revision = "4f9c1d27_locktax"
down_revision = "d5ac554dce56"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the existing CHECK constraint and replace it with the new
    # union of allowed states. We keep the old ones (passover_done,
    # closer_done, etc.) in the allowed set so we don't break Phase 0
    # — the wipe runs against this same Postgres schema. Once Phase 7
    # smoke test passes and confirms no legacy state appears, a later
    # migration can trim them.
    op.execute(
        "ALTER TABLE customer_deals DROP CONSTRAINT IF EXISTS ck_customer_deals_lifecycle_status"
    )
    op.execute(
        """
        ALTER TABLE customer_deals ADD CONSTRAINT ck_customer_deals_lifecycle_status
        CHECK (
            lifecycle_status IS NULL OR lifecycle_status IN (
                'open',
                'lead_gen_done',
                'pre_sales_done',
                'verbal_done',
                'loa_done',
                'verified',
                'rejected',
                -- back-compat (will be dropped post Phase 0 wipe):
                'passover_done',
                'closer_done',
                'c_call_done',
                'amendment_done'
            )
        )
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE customer_deals DROP CONSTRAINT IF EXISTS ck_customer_deals_lifecycle_status"
    )
    op.execute(
        """
        ALTER TABLE customer_deals ADD CONSTRAINT ck_customer_deals_lifecycle_status
        CHECK (
            lifecycle_status IS NULL OR lifecycle_status IN (
                'open',
                'lead_gen_done',
                'passover_done',
                'closer_done',
                'c_call_done',
                'amendment_done',
                'verified',
                'rejected'
            )
        )
        """
    )
