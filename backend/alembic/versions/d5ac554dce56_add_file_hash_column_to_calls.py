"""add file_hash column to calls

Revision ID: d5ac554dce56
Revises: 4ccd8ce8e7e0
Create Date: 2026-05-11 17:24:15.509922

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5ac554dce56'
down_revision: Union[str, None] = '4ccd8ce8e7e0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Production DB already has `file_hash` (was added directly via
    # db.create_all in an earlier deploy). CI's fresh Postgres doesn't,
    # so tests blow up on insert. Use IF NOT EXISTS so this no-ops on
    # prod and creates the column on fresh DBs.
    op.execute("ALTER TABLE calls ADD COLUMN IF NOT EXISTS file_hash VARCHAR")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_calls_file_hash ON calls (file_hash)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_calls_file_hash")
    op.execute("ALTER TABLE calls DROP COLUMN IF EXISTS file_hash")
