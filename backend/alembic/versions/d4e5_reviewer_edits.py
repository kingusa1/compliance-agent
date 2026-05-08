"""reviewer_edits audit table

Captures every inline edit on /tracker — field, old/new, reviewer_id,
timestamp. Used by the Phase C "Previously AI: X" tooltip and forensic
review.

Revision ID: d4e5a6b7c8d9
Revises: c0d3a1b2c3d4
Create Date: 2026-05-05
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "d4e5a6b7c8d9"
down_revision = "c0d3a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reviewer_edits",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("rejection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("field", sa.String(64), nullable=False),
        sa.Column("old_value", sa.Text),
        sa.Column("new_value", sa.Text),
        sa.Column("reviewer_id", sa.String(64)),
        sa.Column(
            "at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_reviewer_edits_rejection_id",
        "reviewer_edits",
        ["rejection_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_reviewer_edits_rejection_id", table_name="reviewer_edits")
    op.drop_table("reviewer_edits")
