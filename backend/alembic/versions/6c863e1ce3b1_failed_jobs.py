"""failed_jobs

Revision ID: 6c863e1ce3b1
Revises: d4e5a6b7c8d9
Create Date: 2026-05-06 20:35:33.379308

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '6c863e1ce3b1'
down_revision: Union[str, None] = 'd4e5a6b7c8d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 2026-05-13: prod already has this table (the original migration ran
    # then alembic_version got desynced — exact history lost). Use raw
    # IF NOT EXISTS SQL so the upgrade is idempotent and doesn't block
    # downstream migrations (incl. the 2026-05-12 taxonomy + segment
    # column adds) from running.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS failed_jobs (
            id          VARCHAR PRIMARY KEY DEFAULT gen_random_uuid()::text,
            call_id     VARCHAR NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
            last_step   VARCHAR(64) NOT NULL,
            attempts    INTEGER NOT NULL DEFAULT 0,
            last_error  TEXT,
            exhausted_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            created_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_failed_jobs_call_attempt "
        "ON failed_jobs (call_id, attempts)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_failed_jobs_exhausted_at "
        "ON failed_jobs (exhausted_at)"
    )


def downgrade() -> None:
    op.drop_index("ix_failed_jobs_exhausted_at", table_name="failed_jobs")
    op.drop_index("ix_failed_jobs_call_attempt", table_name="failed_jobs")
    op.drop_table("failed_jobs")
