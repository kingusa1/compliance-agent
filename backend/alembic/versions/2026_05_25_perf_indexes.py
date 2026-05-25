"""Performance indexes for tracker filters + stuck-call watchdog.

Revision ID: 2026_05_25_perf_idx
Revises: 2026_05_24_meter_indexes
Create Date: 2026-05-25

Adds three missing indexes that profiling on Supabase + read of the
tracker_aggregator filter shapes flagged as missing:

1. ``ix_calls_detected_supplier`` — covers
   ``Call.detected_supplier.in_(...)`` from the multi-select supplier
   filter on the tracker, calls list, and reviewer queue. Previously a
   sequential scan on every page that filtered by supplier.

2. ``ix_calls_agent_name`` — covers ``Call.agent_name.in_(...)`` from
   the multi-select agent filter on the same surfaces. Same Seq-Scan
   pattern as above.

3. ``ix_calls_watchdog_scan`` — partial composite covering BOTH
   redispatch_watchdog queries (``_STUCK_QUERY`` and
   ``_EXHAUSTED_QUERY`` — same WHERE shape, different
   ``watchdog_redispatch_count`` predicate that runs as a residual
   filter on the index scan output). With the 2026-05-25 `_trace_step`
   wiring of `last_step_started_at`, this index makes the watchdog
   cron's once-per-minute scan an Index Scan of <50 rows instead of
   a Seq Scan on the whole calls table. The partial predicate
   captures the two high-selectivity conditions (last_step_started_at
   IS NOT NULL, completed_at IS NULL); the ``status NOT IN`` check
   from both queries runs as a cheap residual on the indexed rows.

All three use ``CREATE INDEX CONCURRENTLY IF NOT EXISTS`` so the
migration is idempotent and never holds a table-level lock on the hot
``calls`` table. Required: this migration MUST run outside a
transaction — Alembic env.py honours ``transactional_ddl = False`` on
the connection, and the per-statement autocommit annotation below
forces the same behaviour even if the env doesn't.
"""
from __future__ import annotations

from alembic import op


# revision identifiers, used by Alembic.
revision = "2026_05_25_perf_idx"
down_revision = "2026_05_24_meter_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect != "postgresql":
        # SQLite (tests) — emit plain CREATE INDEX, no CONCURRENTLY.
        op.execute("CREATE INDEX IF NOT EXISTS ix_calls_detected_supplier ON calls (detected_supplier)")
        op.execute("CREATE INDEX IF NOT EXISTS ix_calls_agent_name ON calls (agent_name)")
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_calls_watchdog_scan "
            "ON calls (last_step_started_at, completed_at, status)"
        )
        return

    # Postgres — CONCURRENTLY must run outside a transaction.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_calls_detected_supplier "
            "ON calls (detected_supplier) WHERE detected_supplier IS NOT NULL"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_calls_agent_name "
            "ON calls (agent_name) WHERE agent_name IS NOT NULL"
        )
        # Partial composite matching the watchdog's WHERE clause exactly:
        #   last_step_started_at < NOW() - INTERVAL '7 minutes'
        #   AND completed_at IS NULL
        #   AND status NOT IN ('completed', 'failed')
        # Postgres can't store the NOT IN clause in the partial predicate
        # (no IMMUTABLE function for that), so we cover the two
        # high-selectivity predicates (completed_at IS NULL, last_step_started_at
        # IS NOT NULL) and let the planner filter the small candidate set
        # for the status check at scan time.
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_calls_watchdog_scan "
            "ON calls (last_step_started_at, status) "
            "WHERE last_step_started_at IS NOT NULL AND completed_at IS NULL"
        )


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect != "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_calls_detected_supplier")
        op.execute("DROP INDEX IF EXISTS ix_calls_agent_name")
        op.execute("DROP INDEX IF EXISTS ix_calls_watchdog_scan")
        return

    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_calls_detected_supplier")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_calls_agent_name")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_calls_watchdog_scan")
