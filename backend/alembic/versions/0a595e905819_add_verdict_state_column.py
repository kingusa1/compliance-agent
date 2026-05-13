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

    # 2026-05-13: prod schema drifted from alembic_version (these columns
    # already exist in prod). Use raw IF NOT EXISTS so this migration is
    # idempotent and the chain can run to head.
    op.execute(
        "ALTER TABLE calls ADD COLUMN IF NOT EXISTS verdict_state "
        "VARCHAR NOT NULL DEFAULT 'AI_PENDING'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_calls_verdict_state ON calls (verdict_state)"
    )

    op.execute(
        "ALTER TABLE rejections ADD COLUMN IF NOT EXISTS verdict_state "
        "VARCHAR NOT NULL DEFAULT 'AI_PENDING'"
    )
    op.execute(
        "ALTER TABLE rejections ADD COLUMN IF NOT EXISTS confirmed_by VARCHAR"
    )
    op.execute(
        "ALTER TABLE rejections ADD COLUMN IF NOT EXISTS confirmed_at "
        "TIMESTAMP WITH TIME ZONE"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rejections_verdict_state "
        "ON rejections (verdict_state)"
    )

    if is_pg:
        op.execute("UPDATE calls SET verdict_state='AI_PENDING' WHERE verdict_state IS NULL")
        op.execute("UPDATE rejections SET verdict_state='AI_PENDING' WHERE verdict_state IS NULL")


def downgrade() -> None:
    op.drop_index("ix_rejections_verdict_state", table_name="rejections")
    op.drop_column("rejections", "confirmed_at")
    op.drop_column("rejections", "confirmed_by")
    op.drop_column("rejections", "verdict_state")
    op.drop_index("ix_calls_verdict_state", table_name="calls")
    op.drop_column("calls", "verdict_state")
