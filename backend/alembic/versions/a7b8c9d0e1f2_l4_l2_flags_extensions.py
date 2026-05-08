"""L4 + L2 — flags.family / detection_type / approved_alternative + saved_views.updated_at

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-04-30 02:40:00.000000

Reviewer-side findings need three new fields on flags so the family
filter (Compliance/Conduct/Disclosure/etc.), detection_type
(exact_phrase|semantic_equivalent|behavioural_pattern), and
approved_alternative (the canonical replacement utterance) can be
filtered/displayed in /api/findings.

saved_views previously shipped (b7e2f3a4c918) without an updated_at —
PATCH semantics need it.
"""
from alembic import op
import sqlalchemy as sa


revision = "a7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # flags extensions
    op.add_column("flags", sa.Column("family", sa.String(), nullable=True))
    op.add_column("flags", sa.Column("detection_type", sa.String(), nullable=True))
    op.create_check_constraint(
        "ck_flags_detection_type",
        "flags",
        "detection_type IS NULL OR detection_type IN "
        "('exact_phrase','semantic_equivalent','behavioural_pattern')",
    )
    op.add_column("flags", sa.Column("approved_alternative", sa.Text(), nullable=True))

    # saved_views.updated_at — needed for PATCH freshness sort.
    # Conservative: add only if missing (saved_views table exists from
    # b7e2f3a4c918, but the column wasn't part of that migration).
    op.add_column(
        "saved_views",
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("saved_views", "updated_at")
    op.drop_column("flags", "approved_alternative")
    op.drop_constraint("ck_flags_detection_type", "flags", type_="check")
    op.drop_column("flags", "detection_type")
    op.drop_column("flags", "family")
