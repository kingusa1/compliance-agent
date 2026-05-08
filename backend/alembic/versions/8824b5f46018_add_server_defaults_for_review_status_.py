"""add server defaults for review_status and compliance_status

Revision ID: 8824b5f46018
Revises: 243544911129
Create Date: 2026-04-17 22:19:04.387534

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8824b5f46018'
down_revision: Union[str, None] = '243544911129'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        'calls', 'review_status',
        existing_type=sa.String(),
        server_default=sa.text("'unclaimed'"),
        existing_nullable=False,
    )
    op.alter_column(
        'calls', 'compliance_status',
        existing_type=sa.String(),
        server_default=sa.text("'pending'"),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column('calls', 'compliance_status', existing_type=sa.String(), server_default=None, existing_nullable=False)
    op.alter_column('calls', 'review_status',     existing_type=sa.String(), server_default=None, existing_nullable=False)
