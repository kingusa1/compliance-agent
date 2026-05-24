"""Add btree indexes on 15 FK columns missing them.

2026-05-24 — Supabase audit found 15 FK columns with no backing index.
Every JOIN or cascade-delete on these columns falls back to a sequential
scan on the parent table. Most visible symptom on prod: DELETE call
averaged 425ms because `call_checkpoints.call_id` had no index — every
cascade had to seq-scan call_checkpoints to find children.

All indexes created CONCURRENTLY so the migration is non-blocking on
prod. Postgres requires each CREATE INDEX CONCURRENTLY to run outside
a transaction, hence the per-statement autocommit_block.

Priority high (hot path): call_checkpoints.call_id, rejections.fix_assignee_id,
customer_deals.assigned_agent_id, claim_locks/transcript_edits/verdict_history
.review_session_id, script_versions.script_id, calls.script_version_id,
call_segments.script_id.

Priority low (audit / cleanup): the 7 FKs to profiles
(audit_log.actor_id, rejection_audit_log.actor_id, fix_directives.{created_by_id,
fixed_by_id,organization_id}, flags.created_by_id).

Filename / revision id ≤32 chars per BRAIN/00_LAW_OF_ENTERPRISE_GRADE.
"""
from alembic import op


revision = "2026_05_24_fk_idx"
down_revision = "2026_05_24_rej_cascade"
branch_labels = None
depends_on = None


_INDEXES = [
    # (index_name, table, column)
    ("ix_call_checkpoints_call_id_fk", "call_checkpoints", "call_id"),
    ("ix_call_segments_script_id_fk", "call_segments", "script_id"),
    ("ix_calls_script_version_id_fk", "calls", "script_version_id"),
    ("ix_claim_locks_review_session_id_fk", "claim_locks", "review_session_id"),
    ("ix_customer_deals_assigned_agent_id_fk", "customer_deals", "assigned_agent_id"),
    ("ix_rejections_fix_assignee_id_fk", "rejections", "fix_assignee_id"),
    ("ix_script_versions_script_id_fk", "script_versions", "script_id"),
    ("ix_transcript_edits_review_session_id_fk", "transcript_edits", "review_session_id"),
    ("ix_verdict_history_review_session_id_fk", "verdict_history", "review_session_id"),
    # profile-FK group
    ("ix_audit_log_actor_id_fk", "audit_log", "actor_id"),
    ("ix_rejection_audit_log_actor_id_fk", "rejection_audit_log", "actor_id"),
    ("ix_fix_directives_created_by_id_fk", "fix_directives", "created_by_id"),
    ("ix_fix_directives_fixed_by_id_fk", "fix_directives", "fixed_by_id"),
    ("ix_fix_directives_organization_id_fk", "fix_directives", "organization_id"),
    ("ix_flags_created_by_id_fk", "flags", "created_by_id"),
]


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for idx, table, column in _INDEXES:
            op.execute(
                f'CREATE INDEX CONCURRENTLY IF NOT EXISTS {idx} ON {table} ({column})'
            )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for idx, _, _ in _INDEXES:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {idx}")
