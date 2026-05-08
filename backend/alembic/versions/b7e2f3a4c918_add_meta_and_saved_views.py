"""add meta jsonb to calls + saved_views table

Phase J Tasks 26 + 37: JSONB call metadata for rich queue filtering and
saved view presets for reviewer productivity.

Revision ID: b7e2f3a4c918
Revises: c83a1f4e7d20
Create Date: 2026-04-18 12:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "b7e2f3a4c918"
down_revision = "c83a1f4e7d20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Task 37: JSONB meta column on calls
    op.add_column(
        "calls",
        sa.Column(
            "meta",
            postgresql.JSONB(),
            server_default="{}",
            nullable=False,
        ),
    )
    op.execute("CREATE INDEX calls_meta_gin ON calls USING GIN (meta)")

    # Task 26: saved_views table
    op.create_table(
        "saved_views",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("owner_id", sa.String(), nullable=False, index=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("filters", sa.Text(), nullable=False),
        sa.Column("is_shared", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("saved_views")
    op.execute("DROP INDEX IF EXISTS calls_meta_gin")
    op.drop_column("calls", "meta")
