"""L1 enterprise sprint — call step tracking columns for durability watchdog

Revision ID: a1b2c3d4e5f6
Revises: 9bf52298c06e
Create Date: 2026-04-30 01:00:00.000000

Adds the four columns the L1 durability layer reads/writes:
  - last_step_started_at: set by _logged_step before each step, cleared on success
  - last_step_name: which step is currently in flight
  - last_step_error: last error message (truncated, plain text)
  - watchdog_redispatch_count: caps redispatch_watchdog at 1 redispatch per call

Partial index on (last_step_started_at) where completed_at IS NULL keeps the
watchdog cron's stuck-call query fast.
"""
from alembic import op
import sqlalchemy as sa


revision = "a1b2c3d4e5f6"
down_revision = "9bf52298c06e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "calls",
        sa.Column("last_step_started_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column("calls", sa.Column("last_step_name", sa.Text(), nullable=True))
    op.add_column("calls", sa.Column("last_step_error", sa.Text(), nullable=True))
    op.add_column(
        "calls",
        sa.Column(
            "watchdog_redispatch_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.create_index(
        "idx_calls_last_step_started_at",
        "calls",
        ["last_step_started_at"],
        postgresql_where=sa.text("completed_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_calls_last_step_started_at", table_name="calls")
    op.drop_column("calls", "watchdog_redispatch_count")
    op.drop_column("calls", "last_step_error")
    op.drop_column("calls", "last_step_name")
    op.drop_column("calls", "last_step_started_at")
