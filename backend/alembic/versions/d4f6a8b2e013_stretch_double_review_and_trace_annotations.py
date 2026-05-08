"""double-review columns + trace_annotations table

Phase J stretch Tasks 25 + 35:
- T25: required_reviews / completed_reviews on calls for double-review mode
- T35: trace_annotations table for per-step reviewer feedback on agent reasoning

Revision ID: d4f6a8b2e013
Revises: b7e2f3a4c918
Create Date: 2026-04-18 14:00:00.000000

"""

from alembic import op
import sqlalchemy as sa

revision = "d4f6a8b2e013"
down_revision = "b7e2f3a4c918"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Task 25: double-review columns
    op.add_column(
        "calls",
        sa.Column("required_reviews", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "calls",
        sa.Column("completed_reviews", sa.Integer(), server_default="0", nullable=False),
    )

    # Task 35: trace annotations
    op.create_table(
        "trace_annotations",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("trace_id", sa.String(), sa.ForeignKey("agent_traces.id"), nullable=False, index=True),
        sa.Column("actor_id", sa.String(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("trace_annotations")
    op.drop_column("calls", "completed_reviews")
    op.drop_column("calls", "required_reviews")
