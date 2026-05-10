"""Add ON DELETE CASCADE to all 9 child tables that FK to calls.id.

Before this migration, `DELETE /api/calls/{id}` returned HTTP 500 on any
completed call because the routes.py endpoint only deletes
CallCheckpoint + Call and PostgreSQL fired a FK violation against any
of these 9 child tables that had rows for the call.

After this migration, deleting a Call cascades cleanly through every
child table.

Revision ID: 20260510_cascade
Revises: d4e5a6b7c8d9, f1a2b3c4d5e6 (merge of the two existing heads)
Create Date: 2026-05-10
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260510_cascade"
down_revision = ("d4e5a6b7c8d9", "f1a2b3c4d5e6")  # merge two heads
branch_labels = None
depends_on = None


# Tables whose `call_id` FK was missing ON DELETE CASCADE.
# The constraint name pattern in PostgreSQL is `<table>_call_id_fkey`
# unless renamed. We drop-and-recreate to add the cascade rule.
TABLES = [
    # (table, column, fk_constraint_name)
    ("call_checkpoints", "call_id", None),
    ("review_sessions", "call_id", None),
    ("verdict_history", "call_id", None),
    ("transcript_edits", "call_id", None),
    ("claim_locks", "call_id", None),
    ("compliance_decisions", "call_id", None),
    ("verdict_suggestions", "call_id", None),
    ("verdict_responses", "call_id", None),
    ("agent_traces", "call_id", None),
]


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    for table, col, _ in TABLES:
        if not insp.has_table(table):
            # Older deploys may not have every table; skip silently.
            continue
        # Find any FK on this column pointing at calls.id.
        fks = insp.get_foreign_keys(table)
        target_fks = [
            fk for fk in fks
            if fk.get("referred_table") == "calls"
            and col in (fk.get("constrained_columns") or [])
        ]
        for fk in target_fks:
            name = fk.get("name")
            if not name:
                continue
            # Drop and recreate with ondelete=CASCADE.
            op.drop_constraint(name, table, type_="foreignkey")
            op.create_foreign_key(
                name,
                table,
                "calls",
                [col],
                ["id"],
                ondelete="CASCADE",
            )


def downgrade() -> None:
    """Recreate the FKs without ON DELETE CASCADE."""
    bind = op.get_bind()
    insp = sa.inspect(bind)

    for table, col, _ in TABLES:
        if not insp.has_table(table):
            continue
        fks = insp.get_foreign_keys(table)
        target_fks = [
            fk for fk in fks
            if fk.get("referred_table") == "calls"
            and col in (fk.get("constrained_columns") or [])
        ]
        for fk in target_fks:
            name = fk.get("name")
            if not name:
                continue
            op.drop_constraint(name, table, type_="foreignkey")
            op.create_foreign_key(
                name,
                table,
                "calls",
                [col],
                ["id"],
            )
