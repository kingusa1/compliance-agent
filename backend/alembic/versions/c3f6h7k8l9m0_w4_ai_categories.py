"""W4.7 watt-coverage — AI category/remediation suggestion on call_checkpoints

Revision ID: c3f6h7k8l9m0
Revises: c2e5g8h3i4j5
Create Date: 2026-05-04 09:00:00.000000

Wave 4 of v3-watt-coverage harness — W4.7 (AI category + remediation_action
suggestion on every checkpoint verdict).

Adds 3 nullable columns to ``call_checkpoints``:

    ai_category              TEXT NULL   — one of REJECTION_CATEGORIES (or NULL)
    ai_fix_required          TEXT NULL   — one of REMEDIATION_ACTIONS  (or NULL)
    ai_category_confidence   REAL NULL   — 0.0 — 1.0

Why: the checkpoint analyzer today returns only PASS/FAIL/PARTIAL +
reasoning. ``rejections_routes.infer_category`` then runs a 7-rule keyword
heuristic over (reason + rule_id) to assign one of Watt's 8 buckets and
defaults to ADMIN_ERROR when nothing matches — wrong roughly half the time
in production. With these 3 columns the analyzer can record Claude's own
suggested bucket + remediation action + confidence, and
``auto_create_rejection_for_verdict`` prefers the AI's suggestion when
confidence ≥ 0.7 (else falls back to the heuristic so we never block on a
missing field).

Additive only — no existing reader cares about these columns and they
default to NULL on every row that pre-dates this migration.

Chain ordering: this revision is W4.7. It chains AFTER W4.B
(``c2e5g8h3i4j5_w4_script_lines.py``, the script_line_mappings + scripts
endpoint pair). The chain ID is fixed in advance so parallel agents can
write into a known DAG even when one branch's worktree hasn't merged yet.
"""
from alembic import op
import sqlalchemy as sa


revision = "c3f6h7k8l9m0"
down_revision = "c2e5g8h3i4j5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "call_checkpoints",
        sa.Column("ai_category", sa.Text(), nullable=True),
    )
    op.add_column(
        "call_checkpoints",
        sa.Column("ai_fix_required", sa.Text(), nullable=True),
    )
    op.add_column(
        "call_checkpoints",
        sa.Column("ai_category_confidence", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("call_checkpoints", "ai_category_confidence")
    op.drop_column("call_checkpoints", "ai_fix_required")
    op.drop_column("call_checkpoints", "ai_category")
