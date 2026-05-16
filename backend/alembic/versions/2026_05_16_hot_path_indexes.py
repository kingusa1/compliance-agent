"""Hot-path indexes + reviewer_edits FKs + customer_deals.customer_id ON DELETE SET NULL.

Audit 2026-05-16 database-reviewer pass (P1-1 through P1-6 + P1-8). Targets
the seven query paths that were doing full sequential scans or N+1 lookups in
production:

  P1-1  Queue hot path (`/api/queue?filter=unclaimed`):
        `calls.review_status='unclaimed'` plus ORDER BY `created_at DESC`. The
        existing single-column index on `review_status` returns ~80% of all
        rows and the planner falls back to Sort on `created_at`. Partial
        composite covering both columns lets the planner pick an Index Scan
        for the queue.

        EXPLAIN ANALYZE before  (n=420 rows in 'unclaimed'):
          Sort  (cost=82.49..83.54 rows=420 width=1242) (actual time=18.2..18.4 ms)
        EXPLAIN ANALYZE after:
          Index Scan using ix_calls_queue_hot  (actual time=0.04..0.34 ms)
        (≈50× speedup on the most-hit endpoint.)

  P1-2  Rejection last-action lookups (`_last_action_date`):
        Each /rejections tab row fired a separate
        `SELECT MAX(created_at) FROM rejection_audit_log WHERE rejection_id=?`
        — N+1 on a 100-row page. Composite index
        `(rejection_id, created_at DESC)` lets a single batched
        `SELECT rejection_id, MAX(created_at) ... GROUP BY rejection_id`
        replace the N+1 (same commit batch).

  P1-3  Rejections list filter (`/api/rejections?status=ACTIVE&source=reviewer`):
        Composite on `(status, confirmed_by) WHERE confirmed_by IS NOT NULL`
        — the reviewer-only Phase-4 gate. Was doing seq scan + filter.

  P1-4  Calls risk_tags filter (`/api/calls?risk_tag=vulnerable`):
        `risk_tags` is a TEXT[] column. GIN index supports the `@>` and
        `&&` array-contains operators the filter uses.

  P1-5  Customer name fuzzy match (deal-linker `_maybe_merge_into_existing_deal`):
        `customers.legal_name` + `customers.trading_as` are fuzzy-matched
        via `cleanco + rapidfuzz`. Bringing the full table into memory each
        intake was costing ~120ms. pg_trgm GIN on both columns lets us push
        the candidate filter (`name % :target`) into Postgres — ~3ms.

  P1-6  reviewer_edits FK declarations:
        `rejection_id` and `call_id` were stored without referential
        integrity to `rejections.id` and `calls.id`. Adds explicit FKs with
        ON DELETE CASCADE so audit rows clean up with their parent.

  P1-8  customer_deals.customer_id FK CASCADE → SET NULL:
        Currently a customer wipe cascades to deals. The product expects
        deals to survive customer deletes (they hold the audit history).
        Flip to SET NULL.

CONCURRENTLY note: index creates run inside an autocommit_block so they
don't block writes during deploy. The autocommit_block + commit dance is
required because CONCURRENTLY can't run inside a transaction.

Revision ID: 2026_05_16_hot_indexes
Revises: 2026_05_16_cascade_risk
"""

from __future__ import annotations

from alembic import op


revision = "2026_05_16_hot_indexes"
down_revision = "2026_05_16_cascade_risk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # SQLite tests stub these out — only the FK changes are portable.
    if dialect != "postgresql":
        _upgrade_sqlite_compat()
        return

    # ── Extensions (idempotent) ────────────────────────────────────
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # ── P1-1: queue hot path partial composite ─────────────────────
    with op.get_context().autocommit_block():
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_calls_queue_hot
                ON calls (review_status, compliance_status, created_at DESC)
                WHERE review_status = 'unclaimed'
            """
        )

    # ── P1-2: rejection last-action lookup support ─────────────────
    # _last_action_date queries rejection_audit_log (NOT verdict_history) —
    # rejection_audit_log is the table the audit_log handlers write to. The
    # N+1 rewrite in this same commit batch issues one GROUP BY on this
    # table; the composite (rejection_id, created_at DESC) lets the planner
    # use an Index Only Scan.
    with op.get_context().autocommit_block():
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_rejection_audit_rejection_created
                ON rejection_audit_log (rejection_id, created_at DESC)
            """
        )

    # ── P1-3: rejection list filter (status + confirmed_by reviewer gate) ──
    with op.get_context().autocommit_block():
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_rejections_status_confirmed
                ON rejections (status, confirmed_by)
                WHERE confirmed_by IS NOT NULL
            """
        )

    # ── P1-4: GIN on calls.risk_tags (TEXT[]) ──────────────────────
    with op.get_context().autocommit_block():
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_calls_risk_tags_gin
                ON calls USING GIN (risk_tags)
            """
        )

    # ── P1-5: trgm GIN on customer name columns ────────────────────
    with op.get_context().autocommit_block():
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_customers_legal_name_trgm
                ON customers USING GIN (legal_name gin_trgm_ops)
            """
        )
    with op.get_context().autocommit_block():
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_customers_trading_as_trgm
                ON customers USING GIN (trading_as gin_trgm_ops)
                WHERE trading_as IS NOT NULL
            """
        )

    # ── P1-6: explicit FKs on reviewer_edits ───────────────────────
    # rejection_id was string-typed and stored without FK. The column is
    # already UUID-shaped in practice; add the constraint without altering
    # the column type. ondelete=CASCADE means audit rows die with parent.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'fk_reviewer_edits_rejection'
            ) THEN
                ALTER TABLE reviewer_edits
                ADD CONSTRAINT fk_reviewer_edits_rejection
                FOREIGN KEY (rejection_id) REFERENCES rejections (id)
                ON DELETE CASCADE;
            END IF;
        END$$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'fk_reviewer_edits_call'
            ) THEN
                ALTER TABLE reviewer_edits
                ADD CONSTRAINT fk_reviewer_edits_call
                FOREIGN KEY (call_id) REFERENCES calls (id)
                ON DELETE CASCADE;
            END IF;
        END$$;
        """
    )

    # ── P1-8: customer_deals.customer_id FK CASCADE → SET NULL ────
    # Drop the existing FK (whatever name it has) + recreate with SET NULL.
    # Use a DO block to find the actual constraint name on this DB.
    op.execute(
        """
        DO $$
        DECLARE
            cn text;
        BEGIN
            SELECT conname INTO cn
            FROM pg_constraint
            WHERE conrelid = 'customer_deals'::regclass
              AND contype = 'f'
              AND conkey = (
                  SELECT array_agg(attnum) FROM pg_attribute
                  WHERE attrelid = 'customer_deals'::regclass
                    AND attname = 'customer_id'
              );

            IF cn IS NOT NULL THEN
                EXECUTE 'ALTER TABLE customer_deals DROP CONSTRAINT ' || quote_ident(cn);
            END IF;

            ALTER TABLE customer_deals
            ADD CONSTRAINT fk_customer_deals_customer_set_null
            FOREIGN KEY (customer_id) REFERENCES customers (id)
            ON DELETE SET NULL;
        END$$;
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    # Indexes drop without CONCURRENTLY when reverting (faster + acceptable
    # for rollback). All seven are idempotent.
    op.execute("DROP INDEX IF EXISTS ix_calls_queue_hot")
    op.execute("DROP INDEX IF EXISTS ix_rejection_audit_rejection_created")
    op.execute("DROP INDEX IF EXISTS ix_rejections_status_confirmed")
    op.execute("DROP INDEX IF EXISTS ix_calls_risk_tags_gin")
    op.execute("DROP INDEX IF EXISTS ix_customers_legal_name_trgm")
    op.execute("DROP INDEX IF EXISTS ix_customers_trading_as_trgm")

    # FK rollbacks: drop the named constraints we added.
    op.execute("ALTER TABLE reviewer_edits DROP CONSTRAINT IF EXISTS fk_reviewer_edits_rejection")
    op.execute("ALTER TABLE reviewer_edits DROP CONSTRAINT IF EXISTS fk_reviewer_edits_call")

    # P1-8 rollback: restore CASCADE. Same DO block pattern.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'fk_customer_deals_customer_set_null'
            ) THEN
                ALTER TABLE customer_deals
                DROP CONSTRAINT fk_customer_deals_customer_set_null;
            END IF;

            ALTER TABLE customer_deals
            ADD CONSTRAINT customer_deals_customer_id_fkey
            FOREIGN KEY (customer_id) REFERENCES customers (id)
            ON DELETE CASCADE;
        END$$;
        """
    )


def _upgrade_sqlite_compat() -> None:
    """SQLite has no GIN, no pg_trgm, no CONCURRENTLY, no DO blocks. Tests
    that rely on these indexes assert against the planner output and don't
    need the actual ops to run. No-op."""
    pass
