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


revision = "2026_05_23_queue_perf_composite_indexes"
down_revision = "2026_05_16_rls_realtime"
branch_labels = None
depends_on = None


# CONCURRENTLY indexes cannot run inside a txn — opt out at the
# migration level so Alembic doesn't wrap us in BEGIN/COMMIT.
def _autocommit_block():
    op.execute("COMMIT")


def upgrade() -> None:
    # Switch into autocommit so CREATE INDEX CONCURRENTLY is accepted.
    _autocommit_block()

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
    _autocommit_block()
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_calls_queue_lookup")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_calls_deal_created_at")
    op.execute(
        "DROP INDEX CONCURRENTLY IF EXISTS ix_calls_completed_with_transcript"
    )
