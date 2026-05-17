"""Enable RLS + Supabase Realtime publication on 11 user-visible tables.

Path 3 / Wave 1 of the 2026-05-16 realtime overhaul.

The compliance-agent frontend authenticates via Supabase Auth (JWT with
role=authenticated). The backend reads/writes via the Supabase
service-role key which BYPASSES RLS — so enabling RLS does NOT break
any backend code path. RLS only gates direct anon/authenticated reads
via Supabase JS — which the frontend has not done historically, but
which Supabase Realtime requires in order to broadcast row changes.

Without RLS in place, ``ALTER PUBLICATION supabase_realtime ADD TABLE x``
would broadcast every INSERT/UPDATE/DELETE on `x` to every connected
client that subscribes to that channel, regardless of permission. With
the policies below in place, Realtime only delivers events that the
subscribing JWT could SELECT — i.e. only active reviewers/leads/admins
see compliance data.

## Tables in scope (user-visible, change frequently)

1. calls — core entity, read by Queue/Tracker/Detail
2. call_checkpoints — per-CP verdicts, read on detail page
3. review_sessions — claim state, drives "Reviewing"/"Read-only" banner
4. verdict_history — audit feed of reviewer actions
5. transcript_edits — collaborative transcript fixes
6. rejections — separate page + Tracker active tab
7. customers — admin customers list
8. customer_deals — admin deals list + tracker join
9. flags — compliance flags shown on call detail
10. profiles — reviewer roster + active state
11. scripts — script catalogue

## Tables explicitly excluded

- saved_views — already polled cheaply, low value
- audit logs / RAG chunks / pipeline_step_log / agent_traces / failed_jobs —
  internal-only, no UI list page
- script_versions / sales_agent_aliases / reference tables — static, no UI
  change-listener

## Policy model

A single SECURITY DEFINER helper function ``is_active_reviewer()`` checks
the calling JWT's profile row. Each table gets ONE SELECT policy that
calls this helper. The function is marked STABLE so Postgres caches its
result per query — single boolean evaluation per query, not per row.

Writes from the frontend are blocked entirely (no policies for
INSERT/UPDATE/DELETE on the authenticated role) — all mutations go
through the Railway backend which uses the service-role key.

## Realtime publication

After RLS, ``ALTER PUBLICATION supabase_realtime ADD TABLE x`` adds each
table to the supabase_realtime publication, which is what the Supabase
Realtime service watches via logical replication. Once added, any client
that subscribes to ``supabase.channel().on('postgres_changes', { table: x })``
receives row events.

Revision ID: 2026_05_16_rls_realtime
Revises: 2026_05_16_hot_indexes
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "2026_05_16_rls_realtime"
down_revision = "2026_05_16_hot_indexes"
branch_labels = None
depends_on = None


TABLES = [
    "calls",
    "call_checkpoints",
    "review_sessions",
    "verdict_history",
    "transcript_edits",
    "rejections",
    "customers",
    "customer_deals",
    "flags",
    "profiles",
    "scripts",
]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite + other test envs don't have RLS or supabase_realtime.
        return

    # CI runs against vanilla Postgres without Supabase's ``auth`` schema
    # or ``supabase_realtime`` publication. The function body below
    # references ``auth.uid()`` and the publication ADD statements
    # require ``supabase_realtime`` to exist — both will hard-fail on
    # plain Postgres. Skip the entire upgrade when those Supabase
    # primitives aren't present so CI (and self-hosted vanilla-Postgres
    # deployments) can still apply ``alembic upgrade head`` cleanly.
    has_auth_schema = bind.execute(
        sa.text(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = 'auth'"
        )
    ).first() is not None
    has_realtime_pub = bind.execute(
        sa.text(
            "SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime'"
        )
    ).first() is not None
    if not has_auth_schema or not has_realtime_pub:
        # Not on Supabase — RLS + realtime are runtime concerns of the
        # managed Postgres only. Bail gracefully.
        return

    # ── Helper function: is_active_reviewer() ──────────────────────
    # SECURITY DEFINER so it can read `profiles` regardless of caller's
    # row-level permissions. STABLE so the planner caches per query.
    # search_path pinned to `public` to prevent search-path hijack.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.is_active_reviewer()
        RETURNS boolean
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = public
        AS $$
            SELECT EXISTS (
                SELECT 1 FROM public.profiles
                WHERE id = (SELECT auth.uid())::text
                  AND role IN ('reviewer', 'lead', 'admin')
                  AND COALESCE(active, true)
            );
        $$;
        """
    )

    # Grant execute to the authenticated role so JWT-bearing clients
    # can call it. anon role does NOT get execute — anonymous clients
    # cannot bypass via the helper.
    op.execute("GRANT EXECUTE ON FUNCTION public.is_active_reviewer() TO authenticated;")

    # ── Enable RLS + SELECT policy per table ───────────────────────
    for table in TABLES:
        # Enable RLS (idempotent — no-op if already enabled).
        op.execute(f'ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY;')

        # Drop any pre-existing policy with this name so re-runs are clean.
        op.execute(
            f'DROP POLICY IF EXISTS "{table}_active_reviewer_select" ON public.{table};'
        )

        # SELECT policy — only active reviewers see rows.
        op.execute(
            f"""
            CREATE POLICY "{table}_active_reviewer_select"
                ON public.{table}
                FOR SELECT
                TO authenticated
                USING (public.is_active_reviewer());
            """
        )

        # Explicit deny for INSERT/UPDATE/DELETE from the authenticated role.
        # Backend uses service_role key which bypasses RLS, so this only
        # blocks direct frontend writes (which the codebase doesn't do today,
        # but defense-in-depth for the future).
        op.execute(
            f'DROP POLICY IF EXISTS "{table}_no_write_from_auth" ON public.{table};'
        )
        op.execute(
            f"""
            CREATE POLICY "{table}_no_write_from_auth"
                ON public.{table}
                FOR ALL
                TO authenticated
                USING (false)
                WITH CHECK (false);
            """
        )

    # ── Add tables to the supabase_realtime publication ────────────
    # Wrap each ADD in DO block so re-adding an existing table is a no-op.
    for table in TABLES:
        op.execute(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_publication_tables
                    WHERE pubname = 'supabase_realtime'
                      AND schemaname = 'public'
                      AND tablename = '{table}'
                ) THEN
                    ALTER PUBLICATION supabase_realtime ADD TABLE public.{table};
                END IF;
            END$$;
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    # Remove from publication (idempotent).
    for table in TABLES:
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM pg_publication_tables
                    WHERE pubname = 'supabase_realtime'
                      AND schemaname = 'public'
                      AND tablename = '{table}'
                ) THEN
                    ALTER PUBLICATION supabase_realtime DROP TABLE public.{table};
                END IF;
            END$$;
            """
        )

    # Drop policies + disable RLS.
    for table in TABLES:
        op.execute(f'DROP POLICY IF EXISTS "{table}_active_reviewer_select" ON public.{table};')
        op.execute(f'DROP POLICY IF EXISTS "{table}_no_write_from_auth" ON public.{table};')
        op.execute(f'ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY;')

    op.execute("DROP FUNCTION IF EXISTS public.is_active_reviewer();")
