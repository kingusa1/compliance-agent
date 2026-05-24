"""Prune the supabase_realtime publication to only subscribed tables.

2026-05-24 Supabase Query Performance dashboard caught the top query
consuming **98.6% of total DB time** (34m 44s · 328,245 calls):

    SELECT wal->>$5 as type, wal->>$6 as schema, ...

That's the Realtime worker's wal2json decoder. It runs for every row
written to a table in the `supabase_realtime` publication.

The publication had **11 tables** but the frontend (via grep of
`useRealtimeInvalidate(...)` in `frontend-v3/src/`) only subscribes to
**5**:

  ✓ calls          ✓ customer_deals    ✓ rejections
  ✓ review_sessions ✓ scripts

The other 6 (`call_checkpoints`, `customers`, `flags`, `profiles`,
`transcript_edits`, `verdict_history`) were being WAL-decoded on every
write and broadcast to zero subscribers. `call_checkpoints` alone
generated 12,498 churn events in this DB's history.

Drop the 6 unused tables. After this migration the realtime worker's
CPU should fall by ~80% based on the relative churn counts. The
matching frontend subscription file is
`frontend-v3/src/lib/hooks/useRealtimeInvalidate.ts` — adding a new
subscription requires adding the table back to this publication.

Idempotent — uses ``DROP TABLE IF EXISTS`` semantics (the
`pg_publication_tables` check below).

Filename / revision id ≤32 chars per BRAIN/00_LAW_OF_ENTERPRISE_GRADE.
"""
from alembic import op


revision = "2026_05_24_rt_prune"
down_revision = "2026_05_24_fk_idx"
branch_labels = None
depends_on = None


_DROP_TABLES = (
    "call_checkpoints",
    "customers",
    "flags",
    "profiles",
    "transcript_edits",
    "verdict_history",
)


def upgrade() -> None:
    for t in _DROP_TABLES:
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM pg_publication_tables
                    WHERE pubname = 'supabase_realtime'
                      AND schemaname = 'public'
                      AND tablename = '{t}'
                ) THEN
                    ALTER PUBLICATION supabase_realtime DROP TABLE public.{t};
                END IF;
            END$$;
            """
        )


def downgrade() -> None:
    for t in _DROP_TABLES:
        op.execute(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_publication_tables
                    WHERE pubname = 'supabase_realtime'
                      AND schemaname = 'public'
                      AND tablename = '{t}'
                ) THEN
                    ALTER PUBLICATION supabase_realtime ADD TABLE public.{t};
                END IF;
            END$$;
            """
        )
