"""W5 sprint — deal.rejection_id + ai_rejection_reason + ai_narrative_notes

Revision ID: e1k0n3p5r7s2
Revises: c4g7i8m9n0o1
Create Date: 2026-05-04 14:50:00.000000

Sprint Lane 1 (A1 + A2 + C2) of v3-watt-coverage 2h sprint.

Bundles three additive column adds into one revision so we don't churn
the migration head twice in a single sprint:

  1. customer_deals.rejection_id UUID NULL FK → rejections(id)
     (Task C2 — back-link the rejection that ended a deal)

  2. call_checkpoints.ai_rejection_reason TEXT NULL
     (Task A1 — Claude's one-line rejection headline for the tracker)

  3. call_checkpoints.ai_narrative_notes TEXT NULL
     (Task A1 — Claude's full coaching narrative for the tracker)

All three are nullable; pre-sprint rows leave them empty and existing
readers ignore the new columns. ``ON DELETE SET NULL`` on the FK so a
rejection delete doesn't cascade-orphan the deal.
"""
from alembic import op


revision = "e1k0n3p5r7s2"
down_revision = "c4g7i8m9n0o1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE customer_deals "
        "ADD COLUMN IF NOT EXISTS rejection_id UUID "
        "REFERENCES rejections(id) ON DELETE SET NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_customer_deals_rejection_id "
        "ON customer_deals (rejection_id)"
    )
    op.execute(
        "ALTER TABLE call_checkpoints "
        "ADD COLUMN IF NOT EXISTS ai_rejection_reason TEXT"
    )
    op.execute(
        "ALTER TABLE call_checkpoints "
        "ADD COLUMN IF NOT EXISTS ai_narrative_notes TEXT"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE call_checkpoints DROP COLUMN IF EXISTS ai_narrative_notes")
    op.execute("ALTER TABLE call_checkpoints DROP COLUMN IF EXISTS ai_rejection_reason")
    op.execute("DROP INDEX IF EXISTS idx_customer_deals_rejection_id")
    op.execute("ALTER TABLE customer_deals DROP COLUMN IF EXISTS rejection_id")
