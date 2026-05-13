"""add pipeline_step_log table

Revision ID: 376c8a03b138
Revises: 8dbb78c954bb
Create Date: 2026-05-08 20:24:08.536063

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


# revision identifiers, used by Alembic.
revision: str = '376c8a03b138'
down_revision: Union[str, None] = '8dbb78c954bb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 2026-05-13: idempotent — prod may already have this table.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS pipeline_step_log (
            id            UUID PRIMARY KEY,
            call_id       VARCHAR NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
            step_name     VARCHAR NOT NULL,
            status        VARCHAR NOT NULL,
            payload_in    JSONB,
            payload_out   JSONB,
            error_message TEXT,
            started_at    TIMESTAMP WITH TIME ZONE NOT NULL,
            ended_at      TIMESTAMP WITH TIME ZONE,
            duration_ms   INTEGER
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_pipeline_step_log_call_id ON pipeline_step_log (call_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_pipeline_step_log_step_name ON pipeline_step_log (step_name)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_pipeline_step_log_status ON pipeline_step_log (status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_pipeline_step_log_started_at ON pipeline_step_log (started_at)")


def downgrade() -> None:
    op.drop_index("ix_pipeline_step_log_started_at", table_name="pipeline_step_log")
    op.drop_index("ix_pipeline_step_log_status", table_name="pipeline_step_log")
    op.drop_index("ix_pipeline_step_log_step_name", table_name="pipeline_step_log")
    op.drop_index("ix_pipeline_step_log_call_id", table_name="pipeline_step_log")
    op.drop_table("pipeline_step_log")
