"""Add ``is_not_applicable`` Boolean to ``call_checkpoints`` so the AI grader
can mark conditional checkpoints (e.g. names containing "if applicable" or
"if relevant") as N/A instead of failing them — closes D10 pattern 1
(analyst report 2026-05-26 estimates ~16 phantom failures per call removed).

The 2026-05-27 analyst report flagged ~21 % of AI verdicts as "clearly
wrong"; pattern 1 alone — conditional checkpoints marked fail when they
should be n_a — accounts for the majority of the loss. With this column
present:

* Pipeline ``_step_score`` excludes ``is_not_applicable`` rows from the
  denominator (score = passed / (total - n_a_count)).
* Frontend renders ``is_not_applicable`` chips muted/grey, distinct from
  pass/fail.
* Existing readers of ``passed`` keep working: we set ``passed=True`` for
  n_a rows so they don't drag the boolean aggregate down. The new flag is
  the load-bearing signal; ``passed`` becomes a derived shortcut.

Design choices:
* New Boolean column (``is_not_applicable``) instead of widening
  ``passed`` to a tri-state enum — keeps the Boolean filter index hot and
  back-compat with every reader of ``passed``.
* ``DEFAULT FALSE`` + ``NOT NULL`` so legacy rows behave as before.
* Idempotent ``ADD COLUMN IF NOT EXISTS`` so the migration is safe to
  re-run.

Revision ID: 2026_05_27_n_a_vocab
Revises: 2026_05_25_perf_idx
Create Date: 2026-05-27
"""
from __future__ import annotations

from alembic import op


revision = "2026_05_27_n_a_vocab"
down_revision = "2026_05_25_perf_idx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add ``is_not_applicable`` Boolean column to ``call_checkpoints``.

    Runs raw SQL so the IF NOT EXISTS guard works on Postgres + SQLite
    (alembic-managed in-memory tests). The ``DEFAULT FALSE NOT NULL``
    ensures every existing row stays interpretable by the prior pipeline
    and every new row produces a deterministic value even if the
    analyzer fails to set the flag explicitly.
    """
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute(
            "ALTER TABLE call_checkpoints "
            "ADD COLUMN IF NOT EXISTS is_not_applicable BOOLEAN NOT NULL DEFAULT FALSE"
        )
        # Partial index on the truthy rows only — n_a rows are rare (~10-30 %
        # of checkpoints per call) so a partial index keeps the size tiny
        # while serving the frontend "show N/A chips" + the score-math
        # "exclude from denominator" queries efficiently.
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_call_checkpoints_is_not_applicable "
            "ON call_checkpoints (call_id) WHERE is_not_applicable = TRUE"
        )
    else:
        # SQLite path used by the CI alembic-upgrade-head smoke test.
        # ``ADD COLUMN`` cannot have IF NOT EXISTS on older SQLite, but the
        # batch-op runs once per migration so re-runs are not an issue here.
        try:
            with op.batch_alter_table("call_checkpoints") as batch_op:
                from sqlalchemy import Boolean, Column
                batch_op.add_column(
                    Column(
                        "is_not_applicable",
                        Boolean(),
                        nullable=False,
                        server_default="0",
                    )
                )
        except Exception:
            # Column already exists — idempotent re-run.
            pass


def downgrade() -> None:
    """Drop ``is_not_applicable`` and its partial index."""
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_call_checkpoints_is_not_applicable")
        op.execute("ALTER TABLE call_checkpoints DROP COLUMN IF EXISTS is_not_applicable")
    else:
        try:
            with op.batch_alter_table("call_checkpoints") as batch_op:
                batch_op.drop_column("is_not_applicable")
        except Exception:
            pass
