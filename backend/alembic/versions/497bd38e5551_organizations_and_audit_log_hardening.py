"""organizations and audit_log hardening

Adds two pieces of multi-tenant + compliance scaffolding without breaking
single-tenant operation:

  1. organizations table + a seeded 'watt' row so future code can stamp
     Watt Utilities records with an organization_id.
  2. organization_id (nullable UUID FK) on every user-facing domain table.
     Nullable on purpose — existing rows belong to no org until backfilled.
  3. audit_log table with prev_hash/this_hash columns for tamper-evident
     append-only history. Used by app.audit.record_audit().

Idempotent: a few of the domain tables already carry an unbacked
organization_id column from earlier Supabase RLS work. The migration
uses ADD COLUMN IF NOT EXISTS and probes pg_constraint before adding
the foreign key, so re-running on a partially-migrated database is
safe and the final shape is identical regardless of starting state.

Revision ID: 497bd38e5551
Revises: 4253da0ac3d9
Create Date: 2026-04-28 03:53:19.849838

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '497bd38e5551'
down_revision: Union[str, None] = '4253da0ac3d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Domain tables that should track which organization owns the row. Listed
# explicitly (not derived) so a future schema change does not silently
# stamp organization_id onto an internal table that should not have it.
ORG_SCOPED_TABLES: tuple[str, ...] = (
    "scripts",
    "script_versions",
    "calls",
    "call_checkpoints",
    "agent_learnings",
    "review_sessions",
    "verdict_history",
    "transcript_edits",
    "claim_locks",
    "compliance_decisions",
    "verdict_suggestions",
    "verdict_responses",
    "profiles",
    "agent_traces",
    "trace_annotations",
    "saved_views",
    "customer_deals",
)


def _add_org_column(table: str) -> None:
    """Add organization_id + FK + index on a domain table, idempotently."""
    op.execute(
        f'ALTER TABLE {table} '
        f'ADD COLUMN IF NOT EXISTS organization_id uuid'
    )
    # Coerce pre-existing non-UUID columns from earlier Supabase RLS work.
    # Empty strings are normalized to NULL first so the cast cannot fail
    # on legacy rows.
    op.execute(
        f"""
        DO $$
        DECLARE
            current_type text;
        BEGIN
            SELECT data_type INTO current_type
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = '{table}'
              AND column_name = 'organization_id';
            IF current_type IS NOT NULL AND current_type <> 'uuid' THEN
                EXECUTE 'UPDATE {table} SET organization_id = NULL '
                     || 'WHERE organization_id !~* '
                     || '''^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}$''';
                EXECUTE 'ALTER TABLE {table} '
                     || 'ALTER COLUMN organization_id TYPE uuid '
                     || 'USING organization_id::uuid';
            END IF;
        END$$;
        """
    )
    constraint = f"fk_{table}_organization_id"
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = '{constraint}'
            ) THEN
                ALTER TABLE {table}
                ADD CONSTRAINT {constraint}
                FOREIGN KEY (organization_id)
                REFERENCES organizations (id)
                ON DELETE SET NULL;
            END IF;
        END$$;
        """
    )
    op.execute(
        f'CREATE INDEX IF NOT EXISTS idx_{table}_organization_id '
        f'ON {table} (organization_id)'
    )


def _drop_org_column(table: str) -> None:
    op.execute(f"DROP INDEX IF EXISTS idx_{table}_organization_id")
    op.execute(
        f"ALTER TABLE {table} "
        f"DROP CONSTRAINT IF EXISTS fk_{table}_organization_id"
    )
    op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS organization_id")


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("slug", sa.Text(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.execute(
        "INSERT INTO organizations (slug, name) VALUES ('watt', 'Watt Utilities') "
        "ON CONFLICT (slug) DO NOTHING"
    )

    for table in ORG_SCOPED_TABLES:
        _add_org_column(table)

    op.create_table(
        "audit_log",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "occurred_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "actor_id",
            sa.String(),
            sa.ForeignKey("profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Text(), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("prev_hash", sa.Text(), nullable=True),
        sa.Column("this_hash", sa.Text(), nullable=False),
    )
    op.create_index(
        "idx_audit_log_org_time",
        "audit_log",
        ["organization_id", sa.text("occurred_at DESC")],
    )
    op.create_index(
        "idx_audit_log_entity",
        "audit_log",
        ["entity_type", "entity_id", sa.text("occurred_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("idx_audit_log_entity", table_name="audit_log")
    op.drop_index("idx_audit_log_org_time", table_name="audit_log")
    op.drop_table("audit_log")

    for table in ORG_SCOPED_TABLES:
        _drop_org_column(table)

    op.drop_table("organizations")
