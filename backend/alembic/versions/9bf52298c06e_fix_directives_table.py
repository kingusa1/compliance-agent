"""fix_directives table

Reviewer-raised follow-up items on a call. Lifecycle:
    pending -> in_progress -> fixed
    in_progress -> dead   (won't fix)

Persisted so the demo walk can show DEMO-05 — state transitions
enforced at the endpoint layer.

Revision ID: 9bf52298c06e
Revises: 497bd38e5551
Create Date: 2026-04-29 00:02:42.289587

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '9bf52298c06e'
down_revision: Union[str, None] = '497bd38e5551'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fix_directives",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "call_id",
            sa.String(),
            sa.ForeignKey("calls.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "created_by_id",
            sa.String(),
            sa.ForeignKey("profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("fixed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "idx_fix_directives_status",
        "fix_directives",
        ["status"],
    )
    op.create_index(
        "idx_fix_directives_call_status",
        "fix_directives",
        ["call_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("idx_fix_directives_call_status", table_name="fix_directives")
    op.drop_index("idx_fix_directives_status", table_name="fix_directives")
    op.drop_table("fix_directives")
