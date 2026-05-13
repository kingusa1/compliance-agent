"""add fix_narrative on rejections

Revision ID: 8dbb78c954bb
Revises: 0a595e905819
Create Date: 2026-05-08 19:33:31.715518

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8dbb78c954bb'
down_revision: Union[str, None] = '0a595e905819'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 2026-05-13: idempotent — prod schema may already have this column
    # (alembic_version drift). IF NOT EXISTS keeps the chain unblocked.
    op.execute("ALTER TABLE rejections ADD COLUMN IF NOT EXISTS fix_narrative TEXT")


def downgrade() -> None:
    op.drop_column("rejections", "fix_narrative")
