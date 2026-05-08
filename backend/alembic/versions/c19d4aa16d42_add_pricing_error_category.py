"""add pricing_error category

Revision ID: c19d4aa16d42
Revises: 6c863e1ce3b1
Create Date: 2026-05-08 18:58:17.583659

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c19d4aa16d42'
down_revision: Union[str, None] = '6c863e1ce3b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE rejection_category ADD VALUE IF NOT EXISTS 'PRICING_ERROR'")


def downgrade() -> None:
    # Postgres cannot remove enum values without rebuilding the type — left as no-op.
    pass
