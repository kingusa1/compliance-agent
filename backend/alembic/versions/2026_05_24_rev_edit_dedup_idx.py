"""Composite index for reviewer_edits dedup tooltip lookup.

2026-05-24 wiring audit C10 — the side-panel "Previously AI:" tooltip
queries reviewer_edits by (rejection_id|call_id, field, at DESC) to
show the most-recent override history. The existing single-column
indexes on rejection_id and call_id force per-row sorts.

This composite index covers both the rejection-keyed and call-keyed
lookups with a single declaration, and supports the 2-second dedup
guard in `_record_reviewer_edit` (tracker_edit_routes.py) without
touching write-path latency.

Non-unique because unique constraints across nullable composite keys
in Postgres require per-engine partial indexes — the app-level dedup
in tracker_edit_routes covers the StrictMode double-invoke surface.

Filename / revision id ≤32 chars per BRAIN/00_LAW_OF_ENTERPRISE_GRADE.
"""
from alembic import op


revision = "2026_05_24_rev_edit_idx"
down_revision = "2026_05_23_q_perf_idx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS
                ix_reviewer_edits_target_field_at
            ON reviewer_edits (
                COALESCE(rejection_id::text, ''),
                COALESCE(call_id, ''),
                field,
                at DESC
            )
            """
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS ix_reviewer_edits_target_field_at"
        )
