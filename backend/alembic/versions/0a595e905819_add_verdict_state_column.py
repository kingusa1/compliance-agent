"""add verdict_state column

Revision ID: 0a595e905819
Revises: c19d4aa16d42
Create Date: 2026-05-08 19:15:06.447873

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0a595e905819'
down_revision: Union[str, None] = 'c19d4aa16d42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    op.add_column(
        "calls",
        sa.Column("verdict_state", sa.String(), nullable=False, server_default="AI_PENDING"),
    )
    op.create_index("ix_calls_verdict_state", "calls", ["verdict_state"])

    op.add_column(
        "rejections",
        sa.Column("verdict_state", sa.String(), nullable=False, server_default="AI_PENDING"),
    )
    op.add_column("rejections", sa.Column("confirmed_by", sa.String(), nullable=True))
    op.add_column(
        "rejections",
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_rejections_verdict_state", "rejections", ["verdict_state"])

    if is_pg:
        # Backfill: any pre-existing row gets AI_PENDING explicitly. server_default
        # already covers new rows; this is for rows that existed before this migration.
        op.execute("UPDATE calls SET verdict_state='AI_PENDING' WHERE verdict_state IS NULL")
        op.execute("UPDATE rejections SET verdict_state='AI_PENDING' WHERE verdict_state IS NULL")


def downgrade() -> None:
    op.drop_index("ix_rejections_verdict_state", table_name="rejections")
    op.drop_column("rejections", "confirmed_at")
    op.drop_column("rejections", "confirmed_by")
    op.drop_column("rejections", "verdict_state")
    op.drop_index("ix_calls_verdict_state", table_name="calls")
    op.drop_column("calls", "verdict_state")
