"""add customer_deals and deal_id fk on calls

Revision ID: 4253da0ac3d9
Revises: f1a2b3c4d5e6
Create Date: 2026-04-23 18:03:51.098697

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '4253da0ac3d9'
down_revision: Union[str, None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "customer_deals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("customer_name", sa.Text(), nullable=False),
        sa.Column("supplier", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'in_progress'")),
        sa.Column("deal_value_gbp", sa.Numeric(), nullable=True),
        sa.Column("mpan_or_mprn", sa.Text(), nullable=True),
        sa.Column("expected_live_date", sa.Date(), nullable=True),
        sa.Column("final_score", sa.Numeric(), nullable=True),
        sa.Column("final_action", sa.Text(), nullable=True),
        sa.Column("risk_tags", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("rejection_category", sa.Text(), nullable=True),
        sa.Column("assigned_agent_id", sa.String(), sa.ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True),
        sa.Column("pipeline_workflow_id", sa.Text(), nullable=True),
    )
    op.create_index("idx_deals_customer_name", "customer_deals", ["customer_name"])
    op.create_index("idx_deals_status", "customer_deals", ["status"])

    op.add_column("calls", sa.Column("deal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("customer_deals.id", ondelete="SET NULL"), nullable=True))
    op.add_column("calls", sa.Column("call_type", sa.Text(), nullable=True))
    op.add_column("calls", sa.Column("supplier_variant", sa.Text(), nullable=True))
    op.create_index("idx_calls_deal_id", "calls", ["deal_id"])


def downgrade() -> None:
    op.drop_index("idx_calls_deal_id", table_name="calls")
    op.drop_column("calls", "supplier_variant")
    op.drop_column("calls", "call_type")
    op.drop_column("calls", "deal_id")
    op.drop_index("idx_deals_status", table_name="customer_deals")
    op.drop_index("idx_deals_customer_name", table_name="customer_deals")
    op.drop_table("customer_deals")
