"""Make FK CASCADE explicit on calls.id children + add 'vulnerable' to risk-tag CHECK.

Audit 2026-05-16 (P0-1 + P0-2) — two latent data-integrity gaps:

1. The 2026-05-10 cascade migration used `inspector.get_foreign_keys()` to
   discover constraint names and skipped any FK whose `name` came back as
   None. On Supabase's transaction-pooled pgBouncer connection the
   pg_constraint catalog query CAN return empty names for FKs created
   without an explicit `name=` argument (which is how most of the HITL
   FKs were created). Those FKs may still be without CASCADE today —
   meaning DELETE on a call with HITL artefacts (ReviewSession,
   VerdictHistory, ClaimLock, etc.) still 500s with an FK violation.

   Fix: re-create each FK with an explicit constraint name + ON DELETE
   CASCADE. Idempotent (DROP IF EXISTS + ADD).

2. The `ck_flags_risk_tag` CHECK constraint was created with 4 values
   (`ombudsman` / `mis-selling` / `complaint` / `cancellation`). The
   2026-04 vulnerability work added a 5th risk-tag pill `Vulnerable` in
   the UI + `_RISK_TAGS_ALLOWED` server-side set, but the DB CHECK was
   never widened. The `flags` writer in `extraction/flags.py` happens
   to lowercase its values, so when a vulnerability flag IS written
   with `risk_tag='vulnerable'` the CHECK rejects it and the entire
   transaction rolls back — surfaces as `L2_EXTRACTION_FAILED` in
   Railway logs.

Revision ID: 2026_05_16_cascade_risk
Revises: 2026_05_15_rev_call
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "2026_05_16_cascade_risk"
down_revision = "2026_05_15_rev_call"
branch_labels = None
depends_on = None


# Table → (constraint name, source column → calls.id) tuples. We rebuild
# each FK from scratch with an explicit name so the CASCADE intent is
# visible in `pg_constraint` and not at the mercy of the introspection
# path.
_CASCADES = (
    ("call_checkpoints", "call_checkpoints_call_id_fkey", "call_id"),
    ("review_sessions", "review_sessions_call_id_fkey", "call_id"),
    ("verdict_history", "verdict_history_call_id_fkey", "call_id"),
    ("transcript_edits", "transcript_edits_call_id_fkey", "call_id"),
    ("claim_locks", "claim_locks_call_id_fkey", "call_id"),
    ("compliance_decisions", "compliance_decisions_call_id_fkey", "call_id"),
    ("verdict_suggestions", "verdict_suggestions_call_id_fkey", "call_id"),
    ("verdict_responses", "verdict_responses_call_id_fkey", "call_id"),
    ("agent_traces", "agent_traces_call_id_fkey", "call_id"),
)


def _table_exists(bind, table: str) -> bool:
    row = bind.exec_driver_sql(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = current_schema() AND table_name = %s",
        (table,),
    ).first()
    return row is not None


def upgrade() -> None:
    bind = op.get_bind()
    for table, name, col in _CASCADES:
        if not _table_exists(bind, table):
            # Table from a future migration that isn't on this DB yet —
            # skip cleanly. The next alembic upgrade will pick it up.
            continue
        # Drop any existing FK on this column under ANY name (including
        # the auto-generated one), then re-add with the explicit name.
        bind.exec_driver_sql(
            f"""
            DO $$
            DECLARE
                fk_name text;
            BEGIN
                FOR fk_name IN
                    SELECT conname
                    FROM pg_constraint
                    WHERE conrelid = '{table}'::regclass
                      AND contype = 'f'
                      AND conkey = ARRAY[
                          (SELECT attnum FROM pg_attribute
                           WHERE attrelid = '{table}'::regclass
                             AND attname = '{col}')
                      ]::smallint[]
                LOOP
                    EXECUTE format('ALTER TABLE {table} DROP CONSTRAINT %%I', fk_name);
                END LOOP;
            END $$;
            """
        )
        bind.exec_driver_sql(
            f"ALTER TABLE {table} "
            f"ADD CONSTRAINT {name} "
            f"FOREIGN KEY ({col}) REFERENCES calls(id) ON DELETE CASCADE"
        )

    # P0-2 — widen ck_flags_risk_tag to include 'vulnerable'. Idempotent:
    # drop the constraint if present, re-add with the 5-value set.
    if _table_exists(bind, "flags"):
        bind.exec_driver_sql("ALTER TABLE flags DROP CONSTRAINT IF EXISTS ck_flags_risk_tag")
        bind.exec_driver_sql(
            "ALTER TABLE flags ADD CONSTRAINT ck_flags_risk_tag "
            "CHECK (risk_tag IS NULL OR risk_tag IN ("
            "'ombudsman','mis-selling','complaint','cancellation','vulnerable'"
            "))"
        )


def downgrade() -> None:
    # Best-effort revert: restore the 4-value risk-tag CHECK and leave
    # the cascade alone (downgrading cascade would re-introduce the
    # DELETE-500 bug — refuse silently).
    bind = op.get_bind()
    if _table_exists(bind, "flags"):
        bind.exec_driver_sql("ALTER TABLE flags DROP CONSTRAINT IF EXISTS ck_flags_risk_tag")
        bind.exec_driver_sql(
            "ALTER TABLE flags ADD CONSTRAINT ck_flags_risk_tag "
            "CHECK (risk_tag IS NULL OR risk_tag IN ("
            "'ombudsman','mis-selling','complaint','cancellation'"
            "))"
        )
