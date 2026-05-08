"""W4.6 watt-coverage — dead_reason column on rejections

Revision ID: c4g7i8m9n0o1
Revises: c3f6h7k8l9m0
Create Date: 2026-05-04 12:00:00.000000

Wave 4 of v3-watt-coverage harness — W4.6 (dead-reasons UI on /rejections
Dead tab).

Adds 1 nullable column to ``rejections``:

    dead_reason  TEXT NULL — one of:
        in_contract / customer_debt / wrong_owner / bacs_rejected / hung_up

Plus an index so the Dead-tab filter chips can do a cheap exact-match
filter against any one of the 5 vocab strings.

Why a column rather than an enum: the dead-reason set is small but Watt's
admin team has historically added new buckets (the XLSX deep-dive surfaces
5 today, but the rejection tracker has 8 historical strings). Postgres
ENUMs require an ALTER TYPE migration each time — TEXT + an app-side
allowlist is the safer shape. Validation lives in
``rejections_routes.DEAD_REASONS`` so frontend + backend agree.

Additive only — every existing reader ignores this column and pre-existing
rows default to NULL.

Chain ordering: this revision is W4.6. It chains AFTER W4.7
(``c3f6h7k8l9m0_w4_ai_categories.py``). The chain ID is fixed in advance
so parallel agents can write into a known DAG even when one branch's
worktree hasn't merged yet.
"""
from alembic import op
import sqlalchemy as sa


revision = "c4g7i8m9n0o1"
down_revision = "c3f6h7k8l9m0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rejections",
        sa.Column("dead_reason", sa.Text(), nullable=True),
    )
    op.create_index(
        "idx_rejections_dead_reason",
        "rejections",
        ["dead_reason"],
    )


def downgrade() -> None:
    op.drop_index("idx_rejections_dead_reason", table_name="rejections")
    op.drop_column("rejections", "dead_reason")
