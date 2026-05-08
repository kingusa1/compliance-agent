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
    bind = op.get_bind()
    json_type = JSONB if bind.dialect.name == "postgresql" else sa.Text
    op.create_table(
        "pipeline_step_log",
        sa.Column("id", UUID(as_uuid=True) if bind.dialect.name == "postgresql" else sa.String(), primary_key=True),
        sa.Column("call_id", sa.String(), sa.ForeignKey("calls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("payload_in", json_type(), nullable=True),
        sa.Column("payload_out", json_type(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
    )
    op.create_index("ix_pipeline_step_log_call_id", "pipeline_step_log", ["call_id"])
    op.create_index("ix_pipeline_step_log_step_name", "pipeline_step_log", ["step_name"])
    op.create_index("ix_pipeline_step_log_status", "pipeline_step_log", ["status"])
    op.create_index("ix_pipeline_step_log_started_at", "pipeline_step_log", ["started_at"])


def downgrade() -> None:
    op.drop_index("ix_pipeline_step_log_started_at", table_name="pipeline_step_log")
    op.drop_index("ix_pipeline_step_log_status", table_name="pipeline_step_log")
    op.drop_index("ix_pipeline_step_log_step_name", table_name="pipeline_step_log")
    op.drop_index("ix_pipeline_step_log_call_id", table_name="pipeline_step_log")
    op.drop_table("pipeline_step_log")
