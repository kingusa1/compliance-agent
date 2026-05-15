"""reviewer_edits — make rejection_id nullable + add call_id

The call-meta PATCH endpoint writes audit rows for edits to rows that
have no Rejection yet (awaiting-review state). Today rejection_id is
NOT NULL so those inserts 500, which in turn strips the CORS headers
from the response and surfaces as "Failed to fetch" in the browser.

Migration:
* relax ``rejection_id`` to nullable so call-meta inserts succeed
* add ``call_id`` (String, indexed, nullable) so the audit row still
  identifies the entity it touched
* CHECK constraint ``rejection_id IS NOT NULL OR call_id IS NOT NULL``
  so we never end up with orphan audit rows

Revision ID: 2026_05_15_rev_call
Revises: 2026_05_15_dealmatch
Create Date: 2026-05-15
"""
from alembic import op
import sqlalchemy as sa


revision = "2026_05_15_rev_call"
down_revision = "2026_05_15_dealmatch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("reviewer_edits", "rejection_id", nullable=True)
    op.add_column(
        "reviewer_edits",
        sa.Column("call_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_reviewer_edits_call_id", "reviewer_edits", ["call_id"]
    )
    op.create_check_constraint(
        "ck_reviewer_edits_target",
        "reviewer_edits",
        "rejection_id IS NOT NULL OR call_id IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint("ck_reviewer_edits_target", "reviewer_edits", type_="check")
    op.drop_index("ix_reviewer_edits_call_id", table_name="reviewer_edits")
    op.drop_column("reviewer_edits", "call_id")
    op.alter_column("reviewer_edits", "rejection_id", nullable=False)
