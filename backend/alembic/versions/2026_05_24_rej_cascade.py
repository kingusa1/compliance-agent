"""Flip rejections.call_id FK from ON DELETE SET NULL to ON DELETE CASCADE.

2026-05-24 — owner reported "I deleted every call but the tracker still
shows stuck rejections". Root cause: `rejections.call_id` was created
with ``ondelete='SET NULL'`` (see model line 467). Every DELETE call
in routes.py:3347 cascaded the call_checkpoints + ReviewSession + 7
other child tables via CASCADE, but Rejection rows just had their
`call_id` set to NULL — leaving the Rejection alive as a ghost row with
no parent. Over multiple delete passes this accumulated to 49 orphan
rejections on prod.

The semantic answer: a Rejection IS evidence of a specific call. If the
call is destroyed the Rejection has no referent and should not survive.
Switch to CASCADE so the next DELETE call sweeps its rejections along
with the rest of the child rows.

The matching one-shot cleanup endpoint
`POST /api/admin/sweep-orphans` handles the current backlog and any
future orphans that surface from non-CASCADE code paths.

Filename / revision id ≤32 chars per BRAIN/00_LAW_OF_ENTERPRISE_GRADE.
"""
from alembic import op


revision = "2026_05_24_rej_cascade"
down_revision = "2026_05_24_cust_idx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the existing SET NULL FK + recreate as CASCADE. PostgreSQL
    # only stores one FK constraint per (table, column) so the drop +
    # add must run sequentially in the same transaction. CONCURRENTLY
    # is not supported for FK changes.
    op.execute("ALTER TABLE rejections DROP CONSTRAINT IF EXISTS rejections_call_id_fkey")
    op.execute(
        """
        ALTER TABLE rejections
        ADD CONSTRAINT rejections_call_id_fkey
        FOREIGN KEY (call_id) REFERENCES calls(id) ON DELETE CASCADE
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE rejections DROP CONSTRAINT IF EXISTS rejections_call_id_fkey")
    op.execute(
        """
        ALTER TABLE rejections
        ADD CONSTRAINT rejections_call_id_fkey
        FOREIGN KEY (call_id) REFERENCES calls(id) ON DELETE SET NULL
        """
    )
