"""Partial index on ``call_checkpoints.needs_review`` for stats COUNT.

Wave-20 (2026-05-27, perf P0 carry-forward): ``/api/stats`` counts
``CallCheckpoint.needs_review = true`` rows across the full table. At
250K+ checkpoints and growing, that count is currently a Seq Scan +
Aggregate. Wave-15 added a 10s TTLCache to share the query under
dashboard fan-out, but the underlying scan still costs at scale.

This migration adds a partial B-tree index that covers ONLY the rows
where the flag is TRUE. At ~10% TRUE / 90% FALSE on 250K rows the
index holds ~25K entries (~1 MB) and the planner can satisfy
``COUNT(*) WHERE needs_review = true`` via an Index Only Scan once the
visibility map is current (Postgres docs §11.9). Vacuum-analyze after
the index builds so the first ``/api/stats`` hit picks IOS.

Pattern verified against authoritative sources (general-purpose research
agent ``aea3ae0411b6481ec``, 2026-05-27):
- Postgres docs Example 11.3 (Partial Indexes) — exact pattern
- Heap blog — measured 10× cold / 50× warm improvement on identical
  ``WHERE flag IS TRUE`` shape
- Use-The-Index-Luke (Markus Winand) — canonical queue-flag pattern;
  predicate must match query WHERE exactly so planner uses the index
- Supabase CLI Issue #2898 — known regression on
  ``CREATE INDEX CONCURRENTLY``. Mitigated by running through Alembic's
  ``autocommit_block`` (the same shape used by ``2026_05_25_perf_idx``).

NULL handling: ``WHERE needs_review = true`` correctly excludes NULL
(three-valued logic: ``NULL = true`` evaluates to NULL, not TRUE). The
query predicate uses the same form, so legacy rows with NULL stay
consistent — they're outside both the index AND the count, which is
the intended behavior.

CONCURRENTLY + IF NOT EXISTS for idempotency + zero-lock on the hot
``call_checkpoints`` table. Migration MUST run outside a transaction
(Alembic ``autocommit_block`` handles this on the Postgres path).

Revision ID: 2026_05_27_cp_needs_rev_idx
Revises: 2026_05_27_quality_check
Create Date: 2026-05-27
"""
from __future__ import annotations

from alembic import op


# Revision identifier. 28 chars — under the 32-char Postgres
# ``alembic_version.version_num`` ceiling per LAW_OF_ENTERPRISE_GRADE §1.
revision = "2026_05_27_cp_needs_rev_idx"
down_revision = "2026_05_27_quality_check"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect != "postgresql":
        # SQLite (tests) — emit plain CREATE INDEX. SQLite supports the
        # WHERE clause for partial indexes since 3.8.0 (Sept 2013).
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_call_checkpoints_needs_review "
            "ON call_checkpoints (needs_review) "
            "WHERE needs_review = 1"  # SQLite treats booleans as integers
        )
        return

    # Postgres — CONCURRENTLY must run outside a transaction block.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_call_checkpoints_needs_review "
            "ON call_checkpoints (needs_review) "
            "WHERE needs_review = true"
        )


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect != "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_call_checkpoints_needs_review")
        return

    # CONCURRENTLY on DROP too — never lock the hot table on rollback.
    with op.get_context().autocommit_block():
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS ix_call_checkpoints_needs_review"
        )
