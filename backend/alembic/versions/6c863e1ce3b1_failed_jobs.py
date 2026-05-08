"""failed_jobs

Revision ID: 6c863e1ce3b1
Revises: d4e5a6b7c8d9
Create Date: 2026-05-06 20:35:33.379308

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6c863e1ce3b1'
down_revision: Union[str, None] = 'd4e5a6b7c8d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "failed_jobs",
        sa.Column(
            "id",
            sa.String(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()::text"),
        ),
        sa.Column(
            "call_id",
            sa.String(),
            sa.ForeignKey("calls.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("last_step", sa.String(64), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "exhausted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_failed_jobs_call_attempt",
        "failed_jobs",
        ["call_id", "attempts"],
        unique=True,
    )
    op.create_index(
        "ix_failed_jobs_exhausted_at",
        "failed_jobs",
        ["exhausted_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_failed_jobs_exhausted_at", table_name="failed_jobs")
    op.drop_index("ix_failed_jobs_call_attempt", table_name="failed_jobs")
    op.drop_table("failed_jobs")
