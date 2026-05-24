"""Partial index on customer_deals(LOWER(TRIM(customer_name))) for /customers.

2026-05-24 — the /customers aggregation (list, detail, rollup, timeline)
now filters out placeholder names via `_real_name_predicate` so multiple
"(pending audio upload)" stub deals stop coalescing into one synthetic
customer (owner-reported bug — mixed-supplier customer row showed
E.ON Next + British Gas under one header).

The existing `idx_deals_customer_name` (from `4253da0ac3d9`) is a plain
B-tree on `customer_name`. With the new predicate, every customer-page
hit index-scans that column AND filters by four extra conditions:
``TRIM(...) <> ''``, ``NOT IN (3 literals)``, ``LOWER(...) LIKE``. At
~2k+ rows the planner regresses to a seq scan because the filter
output is hard to estimate.

This partial covering index lets the planner switch to an Index-Only
Scan for both the LIST_SQL `GROUP BY LOWER(TRIM(customer_name))` and
the `LOWER(TRIM(d.customer_name)) = :slug` equality predicate used by
all four detail/rollup/timeline handlers — the index already includes
the LOWER(TRIM(...)) expression and excludes placeholder rows so the
planner can satisfy the query without touching the heap.

Partial predicate mirrors `_real_name_predicate` in
`backend/app/customers_routes.py` — keep these two in sync.

Filename / revision id ≤32 chars per BRAIN/00_LAW_OF_ENTERPRISE_GRADE.
"""
from alembic import op


revision = "2026_05_24_cust_idx"
down_revision = "2026_05_24_seg_bucket_idx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS
                ix_deals_real_customer_name
            ON customer_deals (LOWER(TRIM(customer_name)))
            WHERE customer_name IS NOT NULL
              AND TRIM(customer_name) <> ''
              AND customer_name NOT IN (
                '(pending audio upload)', '(no customer)', 'Untitled'
              )
              AND LOWER(customer_name) NOT LIKE '(auto-detect pending%'
            """
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS ix_deals_real_customer_name"
        )
