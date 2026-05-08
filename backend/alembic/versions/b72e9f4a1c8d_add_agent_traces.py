"""add agent_traces table

Phase J Task 24 — persist per-turn reasoning for each agent batch run so the
HITL UI can surface an expandable "Show AI reasoning" section on a call.

Revision ID: b72e9f4a1c8d
Revises: a61574d004a0
Create Date: 2026-04-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b72e9f4a1c8d"
down_revision: Union[str, None] = "a61574d004a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_traces",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("call_id", sa.String(), nullable=False),
        sa.Column("checkpoint_id", sa.String(), nullable=True),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("turn", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("tool_name", sa.String(), nullable=True),
        sa.Column("tool_input", sa.Text(), nullable=True),
        sa.Column("tool_output", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["call_id"], ["calls.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_agent_traces_call_id"), "agent_traces", ["call_id"])
    op.create_index(op.f("ix_agent_traces_checkpoint_id"), "agent_traces", ["checkpoint_id"])
    op.create_index(op.f("ix_agent_traces_run_id"), "agent_traces", ["run_id"])
    op.create_index(op.f("ix_agent_traces_created_at"), "agent_traces", ["created_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_agent_traces_created_at"), table_name="agent_traces")
    op.drop_index(op.f("ix_agent_traces_run_id"), table_name="agent_traces")
    op.drop_index(op.f("ix_agent_traces_checkpoint_id"), table_name="agent_traces")
    op.drop_index(op.f("ix_agent_traces_call_id"), table_name="agent_traces")
    op.drop_table("agent_traces")
