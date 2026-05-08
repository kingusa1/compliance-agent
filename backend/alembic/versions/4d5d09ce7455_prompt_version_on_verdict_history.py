"""prompt_version on verdict_history

Phase J Task 32 — tag every AI / reviewer verdict with the 12-char sha256 of
the supplier prompt active at the moment the verdict was produced. Enables
A/B analysis ("override rate for prompt v abc123 vs v def456") without having
to re-run the pipeline or spelunk git history.

Nullable: existing rows predate this task and stay NULL. An ix_ index supports
the group-by-version queries documented in
backend/migrations_sql/004_prompt_version_queries.sql.

Revision ID: 4d5d09ce7455
Revises: 0d24da0a1b40
Create Date: 2026-04-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4d5d09ce7455"
down_revision: Union[str, None] = "0d24da0a1b40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "verdict_history",
        sa.Column("prompt_version", sa.String(), nullable=True),
    )
    op.create_index(
        "ix_verdict_history_prompt_version",
        "verdict_history",
        ["prompt_version"],
    )


def downgrade() -> None:
    op.drop_index("ix_verdict_history_prompt_version", table_name="verdict_history")
    op.drop_column("verdict_history", "prompt_version")
