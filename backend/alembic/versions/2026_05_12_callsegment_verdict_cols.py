"""Extend call_segments with per-segment verdict columns + add
call_checkpoints.segment_id FK.

Part of the 2026-05-12 taxonomy rebuild: each AI-classified segment of a
call now carries its own score / bucket / compliance_status so the call
detail UI can show one verdict card per segment and the call-level
verdict aggregates across them.

Revision ID: 7a9d4e1f_segvrd
Revises: 4f9c1d27_locktax
Create Date: 2026-05-12
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "7a9d4e1f_segvrd"
down_revision = "4f9c1d27_locktax"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # All new columns are nullable so the migration is non-destructive
    # for legacy rows (which Phase 0 wipe will delete anyway).
    op.execute(
        """
        ALTER TABLE call_segments
            ADD COLUMN IF NOT EXISTS start_word_idx       INTEGER,
            ADD COLUMN IF NOT EXISTS end_word_idx         INTEGER,
            ADD COLUMN IF NOT EXISTS confidence           NUMERIC,
            ADD COLUMN IF NOT EXISTS classifier_reasoning TEXT,
            ADD COLUMN IF NOT EXISTS script_id            VARCHAR REFERENCES scripts(id) ON DELETE SET NULL,
            ADD COLUMN IF NOT EXISTS score                VARCHAR,
            ADD COLUMN IF NOT EXISTS compliant            BOOLEAN,
            ADD COLUMN IF NOT EXISTS compliance_status    VARCHAR,
            ADD COLUMN IF NOT EXISTS bucket               VARCHAR,
            ADD COLUMN IF NOT EXISTS critical_breaches    INTEGER DEFAULT 0,
            ADD COLUMN IF NOT EXISTS high_breaches        INTEGER DEFAULT 0,
            ADD COLUMN IF NOT EXISTS medium_breaches      INTEGER DEFAULT 0,
            ADD COLUMN IF NOT EXISTS reason               TEXT,
            ADD COLUMN IF NOT EXISTS checkpoint_results   TEXT
        """
    )

    # CallCheckpoint.segment_id — nullable so legacy rows survive.
    op.execute(
        """
        ALTER TABLE call_checkpoints
            ADD COLUMN IF NOT EXISTS segment_id UUID REFERENCES call_segments(id) ON DELETE SET NULL
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_call_checkpoints_segment_id ON call_checkpoints(segment_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_call_checkpoints_segment_id")
    op.execute(
        """
        ALTER TABLE call_checkpoints
            DROP COLUMN IF EXISTS segment_id
        """
    )
    op.execute(
        """
        ALTER TABLE call_segments
            DROP COLUMN IF EXISTS checkpoint_results,
            DROP COLUMN IF EXISTS reason,
            DROP COLUMN IF EXISTS medium_breaches,
            DROP COLUMN IF EXISTS high_breaches,
            DROP COLUMN IF EXISTS critical_breaches,
            DROP COLUMN IF EXISTS bucket,
            DROP COLUMN IF EXISTS compliance_status,
            DROP COLUMN IF EXISTS compliant,
            DROP COLUMN IF EXISTS score,
            DROP COLUMN IF EXISTS script_id,
            DROP COLUMN IF EXISTS classifier_reasoning,
            DROP COLUMN IF EXISTS confidence,
            DROP COLUMN IF EXISTS end_word_idx,
            DROP COLUMN IF EXISTS start_word_idx
        """
    )
