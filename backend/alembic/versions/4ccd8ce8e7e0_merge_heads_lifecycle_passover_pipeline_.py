"""merge heads — lifecycle passover + pipeline step log

Revision ID: 4ccd8ce8e7e0
Revises: 20260511_passover, 376c8a03b138
Create Date: 2026-05-11 17:15:42.644976

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4ccd8ce8e7e0'
down_revision: Union[str, None] = ('20260511_passover', '376c8a03b138')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
