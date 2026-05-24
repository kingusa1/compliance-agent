"""Add composite indexes to kill the slow-query hotspots surfaced by
Supabase pg_stat_statements on 2026-05-23.

Three queries dominated database time on the platform:

1. **Queue list — 46% of total DB time** (7771 calls, mean 80 ms, max 5.5 s):
   ``WHERE review_status = $1 AND compliance_status IN ($2, $3)
     ORDER BY created_at DESC LIMIT $4``
   - Single-column indexes on `review_status` + `compliance_status` +
     `created_at` exist, but Postgres can't combine them efficiently
     for this composite filter + sort.
   - Fix: composite `(review_status, compliance_status, created_at DESC)`
     so the planner walks the index in order and skips an explicit sort.

2. **Deal-grouped calls — ~0.6% time but per-row latency 380–530 ms**:
   ``WHERE deal_id IN (...) ORDER BY created_at ASC``
   - `deal_id` is indexed by itself; the sort needs an in-memory pass
     for each IN-list batch.
   - Fix: composite `(deal_id, created_at)` covers both the filter and
     the sort in one index scan.

3. **Backfill scan — ~0.3% time but full-table on completed-with-transcript**:
   ``WHERE status = 'completed' AND transcript IS NOT NULL ...``
   - Fix: partial index on `created_at DESC WHERE status = 'completed'
     AND transcript IS NOT NULL` keeps the index tiny while accelerating
     the dashboard's recent-calls + every backfill admin endpoint.

Indexes are created CONCURRENTLY so the migration doesn't block writes
on the running production database. CREATE INDEX CONCURRENTLY can NOT
run inside a transaction, so this migration sets
``transactional_ddl = False`` via the helper below.

Read-only safety: indexes never change data; they can be dropped if
the planner picks a worse path. Down-revision deletes all three so
``alembic downgrade`` is a no-op for query results.
"""
from alembic import op


# NOTE 2026-05-23 — Postgres' `alembic_version.version_num` column is
# `VARCHAR(32)`. Revision id MUST be ≤32 chars or every alembic upgrade
# trips `psycopg2.errors.StringDataRightTruncation` on the UPDATE that
# bumps the stored head. The original revision string
# `2026_05_23_queue_perf_composite_indexes` was 39 chars and broke 7
# consecutive CI runs from 15:35 UTC. The new id is 21 chars.
# Filename was renamed to match. Filed in
# [[BRAIN/00_LAW_OF_ENTERPRISE_GRADE]] checklist as a hard rule.
revision = "2026_05_23_q_perf_idx"
down_revision = "2026_05_16_rls_realtime"
branch_labels = None
depends_on = None


# CONCURRENTLY indexes cannot run inside a txn. 2026-05-24 wiring audit C8
# replaced the prior raw `op.execute("COMMIT")` helper with Alembic's
# `op.get_context().autocommit_block()` context manager — matches the
# pattern in 2026_05_16_hot_indexes and keeps Alembic's version-stamp
# rollback semantics intact if a CONCURRENTLY build fails mid-run.


def upgrade() -> None:
    with op.get_context().autocommit_block():
        # 1. Queue hotspot: (review_status, compliance_status, created_at DESC)
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS
                ix_calls_queue_lookup
            ON calls (review_status, compliance_status, created_at DESC)
            """
        )

        # 2. Deal rollup: (deal_id, created_at ASC) — used by the deals detail
        #    page and the customer timeline view.
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS
                ix_calls_deal_created_at
            ON calls (deal_id, created_at)
            """
        )

        # 3. Pipeline / dashboard scan: completed calls with a transcript.
        #    Partial index keeps it small (~50% of rows) and avoids index
        #    bloat on the pending/processing tail.
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS
                ix_calls_completed_with_transcript
            ON calls (created_at DESC)
            WHERE status = 'completed' AND transcript IS NOT NULL
            """
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_calls_queue_lookup")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_calls_deal_created_at")
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS ix_calls_completed_with_transcript"
        )
