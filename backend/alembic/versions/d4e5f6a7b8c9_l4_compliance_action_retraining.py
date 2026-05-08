"""L4 reviewer UX — 5-state compliance action + dead_reason + retraining

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-04-30 02:10:00.000000

Adds:
  - compliance_decisions.action (PASS|REVIEW|COACHING|FAIL|BLOCK)
  - fix_directives.dead_reason (TEXT, free-form why directive was killed)
  - fix_directives.fixed_by_id (FK profiles)
  - fix_directives.status: extend to allow 'submitted' (no enum constraint
    pre-existed; we add a CHECK ensuring known states)
  - profiles.retraining_assigned (BOOLEAN)
  - profiles.retraining_reason (TEXT)
"""
from alembic import op
import sqlalchemy as sa


revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # compliance_decisions: 5-state action vocabulary
    op.add_column("compliance_decisions", sa.Column("action", sa.String(), nullable=True))
    op.create_check_constraint(
        "ck_compliance_decisions_action",
        "compliance_decisions",
        "action IS NULL OR action IN ('PASS','REVIEW','COACHING','FAIL','BLOCK')",
    )

    # fix_directives: dead_reason + fixed_by FK + extended status check
    op.add_column("fix_directives", sa.Column("dead_reason", sa.Text(), nullable=True))
    op.add_column(
        "fix_directives",
        sa.Column(
            "fixed_by_id",
            sa.String(),
            sa.ForeignKey("profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_fix_directives_status",
        "fix_directives",
        "status IN ('pending','in_progress','submitted','fixed','dead')",
    )

    # profiles: retraining flags
    op.add_column(
        "profiles",
        sa.Column(
            "retraining_assigned",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("FALSE"),
        ),
    )
    op.add_column("profiles", sa.Column("retraining_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("profiles", "retraining_reason")
    op.drop_column("profiles", "retraining_assigned")
    op.drop_constraint("ck_fix_directives_status", "fix_directives", type_="check")
    op.drop_column("fix_directives", "fixed_by_id")
    op.drop_column("fix_directives", "dead_reason")
    op.drop_constraint("ck_compliance_decisions_action", "compliance_decisions", type_="check")
    op.drop_column("compliance_decisions", "action")
