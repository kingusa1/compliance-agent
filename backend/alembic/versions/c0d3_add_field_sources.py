"""add field_sources JSONB to customer_deals + rejections

Revision ID: c0d3a1b2c3d4
Revises: e1k0n3p5r7s2
Create Date: 2026-05-05
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "c0d3a1b2c3d4"
down_revision = "e1k0n3p5r7s2"
branch_labels = None
depends_on = None


# Verified against backend/app/models.py:506 (CustomerDeal). `broker` and
# `agent_name` from the plan list don't exist on the model, so they're
# dropped here (assigned_agent_id is the FK; no scalar broker field yet).
_DEAL_FIELDS = (
    "customer_name", "supplier", "mpan_or_mprn", "deal_value_gbp",
    "expected_live_date",
)
# Verified against backend/app/models.py:880 (Rejection). All six exist.
_REJECTION_FIELDS = (
    "supplier", "sales_agent", "category", "rejection_reason",
    "fix_required", "status",
)


def upgrade() -> None:
    op.add_column(
        "customer_deals",
        sa.Column("field_sources", postgresql.JSONB, nullable=False, server_default="{}"),
    )
    op.add_column(
        "rejections",
        sa.Column("field_sources", postgresql.JSONB, nullable=False, server_default="{}"),
    )

    # Backfill existing rows. Stub deals get "placeholder" for every field;
    # everything else gets "xlsx_import" since that's how non-stub rows
    # arrived in the DB before this migration.
    for fields, table in ((_DEAL_FIELDS, "customer_deals"), (_REJECTION_FIELDS, "rejections")):
        kv = ", ".join(f"'{f}', 'xlsx_import'" for f in fields)
        op.execute(f"""
            UPDATE {table}
            SET field_sources = jsonb_build_object({kv})
            WHERE field_sources = '{{}}'::jsonb
            AND ({" OR ".join(f"{f} IS NOT NULL" for f in fields)})
        """)
    op.execute("""
        UPDATE customer_deals
        SET field_sources = '{"customer_name": "placeholder"}'::jsonb
        WHERE customer_name LIKE '(auto-detect pending%' OR customer_name = '(pending audio upload)'
    """)


def downgrade() -> None:
    op.drop_column("rejections", "field_sources")
    op.drop_column("customer_deals", "field_sources")
