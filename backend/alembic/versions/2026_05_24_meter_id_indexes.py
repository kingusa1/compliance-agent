"""Partial indexes on customer_deals meter-id columns.

Why this exists (2026-05-24):
    The new post-extraction merge step (`app.deal_meter_merge`) and the
    admin consolidator (`/api/admin/consolidate-duplicate-deals`) both
    issue a query of the shape:

        SELECT * FROM customer_deals
        WHERE created_at >= now() - interval '365 days'
        AND (mpan_electricity IS NOT NULL
             OR mprn_gas IS NOT NULL
             OR mpan_or_mprn IS NOT NULL);

    Then they canonicalise each meter id in Python and compare to the
    incoming MPAN/MPRN. The canonicalisation can't be pushed into SQL
    because UK MPAN cores are derived from any 13-OR-21-digit input and
    MPRNs are 6-10 digits, so an equality match against the stored raw
    value would miss cross-column writes (a 10-digit MPRN stored in
    `mpan_electricity` is real user-data).

    Without these indexes the per-call merge does a SEQUENTIAL SCAN of
    `customer_deals` on every finalise — fine at 100 rows, terrible at
    100,000. Partial indexes on (`mpan_electricity IS NOT NULL`),
    (`mprn_gas IS NOT NULL`), and (`mpan_or_mprn IS NOT NULL`) collapse
    that to an index-only scan over the (much smaller) set of deals that
    actually have a meter id.

    Mirrors the partial-index style established in
    `2026_05_16_hot_indexes.py` (the project's canonical "add hot indexes"
    migration).

Postgres only. SQLite (used by tests) skips index creation silently —
the index hint is only used by Postgres's planner anyway.
"""
from __future__ import annotations

from alembic import op


# Revision identifiers, used by Alembic.
revision = "2026_05_24_meter_indexes"
down_revision = "2026_05_24_rt_prune"
branch_labels = None
depends_on = None


_INDEX_DEFS = (
    ("idx_customer_deals_mpan_electricity", "mpan_electricity"),
    ("idx_customer_deals_mprn_gas", "mprn_gas"),
    ("idx_customer_deals_mpan_or_mprn", "mpan_or_mprn"),
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite test path — skip cleanly. The cross-dialect pattern
        # established in 2026_05_16_hot_indexes lives here too.
        return
    # `CREATE INDEX CONCURRENTLY` would be nice but Alembic wraps the
    # migration in a transaction by default, and `CONCURRENTLY` cannot
    # run inside a transaction block. Plain `CREATE INDEX IF NOT EXISTS`
    # is fine — these are partial indexes, the build scans only rows
    # with a non-NULL value (typically <50% of customer_deals), and the
    # table is small enough on Supabase Pro that an ACCESS EXCLUSIVE
    # lock for the build is sub-second.
    for name, col in _INDEX_DEFS:
        op.execute(
            f"CREATE INDEX IF NOT EXISTS {name} "
            f"ON customer_deals ({col}) "
            f"WHERE {col} IS NOT NULL"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for name, _ in _INDEX_DEFS:
        op.execute(f"DROP INDEX IF EXISTS {name}")
