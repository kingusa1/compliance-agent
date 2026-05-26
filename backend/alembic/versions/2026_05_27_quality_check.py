"""Add ``quality_check`` JSONB column to ``calls`` table.

Houses the QualityCheckerAgent's audit envelope (verdict / issues /
score / summary / model / checked_at / elapsed_ms). Owner mandate
2026-05-27 — every record gets a second-opinion AI agent that flags
inconsistencies before a human reviewer sees the row.

Stored as JSONB on Postgres so issue-search (e.g. "find all calls
where QC verdict was block") is fast via GIN index. SQLite path uses
TEXT and JSON encoding by app convention.

Revision ID: 2026_05_27_quality_check
Revises: 2026_05_27_n_a_vocab
Create Date: 2026-05-27
"""
from __future__ import annotations

from alembic import op


revision = "2026_05_27_quality_check"
down_revision = "2026_05_27_n_a_vocab"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute(
            "ALTER TABLE calls "
            "ADD COLUMN IF NOT EXISTS quality_check JSONB"
        )
        # Partial expression index on quality_check->>'verdict' so the
        # tracker can scan for "verdict='block'" calls without a
        # sequential scan. Partial on NOT NULL because the column is
        # populated only after the QC agent runs.
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_calls_quality_check_verdict "
            "ON calls ((quality_check->>'verdict')) "
            "WHERE quality_check IS NOT NULL"
        )
    else:
        # SQLite uses TEXT + JSON convention. ADD COLUMN without IF NOT
        # EXISTS — wrapped in try/except for idempotent re-runs.
        try:
            with op.batch_alter_table("calls") as batch_op:
                from sqlalchemy import Text, Column
                batch_op.add_column(
                    Column("quality_check", Text(), nullable=True)
                )
        except Exception:
            pass


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_calls_quality_check_verdict")
        op.execute("ALTER TABLE calls DROP COLUMN IF EXISTS quality_check")
    else:
        try:
            with op.batch_alter_table("calls") as batch_op:
                batch_op.drop_column("quality_check")
        except Exception:
            pass
