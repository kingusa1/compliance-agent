"""Partial covering index for the compliant-strict backfill scan.

2026-05-24 — the new ``/api/admin/backfill-compliant-strict`` endpoint
scans ``call_segments`` filtered by ``bucket IS NOT NULL AND bucket !=
'pass'`` to find calls whose worst segment isn't a clean pass. The
``database-reviewer`` audit flagged this as a seq scan at any scale
past ~30k rows; the same index also speeds up any future per-bucket
analytics query (Compliant tab refresh, tracker filter pivots, etc).

Partial on ``bucket IS NOT NULL`` because graded segments are the only
rows we ever filter on bucket — un-graded rows shouldn't bloat the
index. Column order ``(bucket, call_id)`` puts the equality predicate
first; ``call_id`` is included so the scan is index-only (no heap
fetch to produce the call_id list).

Filename / revision id ≤32 chars per BRAIN/00_LAW_OF_ENTERPRISE_GRADE.
"""
from alembic import op


revision = "2026_05_24_seg_bucket_idx"
down_revision = "2026_05_24_rev_edit_idx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS
                ix_call_segments_bucket_call_id
            ON call_segments (bucket, call_id)
            WHERE bucket IS NOT NULL
            """
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS ix_call_segments_bucket_call_id"
        )
