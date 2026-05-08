"""verdict_suggestions + verdict_responses tables

Phase J stretch Tasks 28 + 31: splits the verdict data model into AI
suggestions and human responses, enabling clean agreement queries and
time-travel reads without mutating the existing VerdictHistory table.

Revision ID: e5a7c9d1f234
Revises: d4f6a8b2e013
Create Date: 2026-04-18 15:00:00.000000

"""

from alembic import op
import sqlalchemy as sa

revision = "e5a7c9d1f234"
down_revision = "d4f6a8b2e013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "verdict_suggestions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("call_id", sa.String(), sa.ForeignKey("calls.id"), nullable=False, index=True),
        sa.Column("checkpoint_id", sa.String(), nullable=False, index=True),
        sa.Column("verdict", sa.String(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.String(), nullable=True, index=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("superseded_by", sa.String(), nullable=True),
    )

    op.create_table(
        "verdict_responses",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("suggestion_id", sa.String(), sa.ForeignKey("verdict_suggestions.id"), nullable=False, index=True),
        sa.Column("call_id", sa.String(), sa.ForeignKey("calls.id"), nullable=False, index=True),
        sa.Column("checkpoint_id", sa.String(), nullable=False, index=True),
        sa.Column("actor_id", sa.String(), nullable=False),
        sa.Column("actor_role", sa.String(), nullable=False),
        sa.Column("verdict", sa.String(), nullable=False),
        sa.Column("agreed_with_ai", sa.Boolean(), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_current", sa.Boolean(), server_default="true", nullable=False, index=True),
    )


def downgrade() -> None:
    op.drop_table("verdict_responses")
    op.drop_table("verdict_suggestions")
