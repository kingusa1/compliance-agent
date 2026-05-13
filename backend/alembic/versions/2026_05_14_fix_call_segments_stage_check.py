"""fix ck_call_segments_stage CHECK to use 4-stage taxonomy

The b2c3d4e5f6a7 migration was authored against the pre-rebuild 6-stage
vocabulary (intro/qualification/pitch/transfer/verbal/close). The
2026-05-12 taxonomy rebuild locked call_type / segment stage to exactly
four values (lead_gen, pre_sales, verbal, loa) but did NOT update this
CHECK — so the content_classifier writes 'pre_sales' / 'loa' and the
INSERT fails with CheckViolation, halting the pipeline mid-flight and
leaving the call stuck at status='processing'.

Fix is dialect-aware:
  - Postgres: DROP CONSTRAINT IF EXISTS then ADD CONSTRAINT with the
    new vocabulary (lead_gen, pre_sales, verbal, loa).
  - SQLite (tests): no-op — SQLite ignores CHECK on ALTER TABLE.

Revision ID: 2026_05_14_stagefix
Revises: 7a9d4e1f_segvrd
Create Date: 2026-05-14
"""
from __future__ import annotations

from alembic import op


# revision identifiers, used by Alembic.
revision = "2026_05_14_stagefix"
down_revision = "7a9d4e1f_segvrd"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite (tests) — no CHECK enforcement on stage values anyway.
        return
    op.execute(
        "ALTER TABLE call_segments DROP CONSTRAINT IF EXISTS ck_call_segments_stage"
    )
    op.execute(
        "ALTER TABLE call_segments "
        "ADD CONSTRAINT ck_call_segments_stage "
        "CHECK (stage IN ('lead_gen','pre_sales','verbal','loa'))"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute(
        "ALTER TABLE call_segments DROP CONSTRAINT IF EXISTS ck_call_segments_stage"
    )
    op.execute(
        "ALTER TABLE call_segments "
        "ADD CONSTRAINT ck_call_segments_stage "
        "CHECK (stage IN ('intro','qualification','pitch','transfer','verbal','close'))"
    )
