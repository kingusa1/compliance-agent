"""profiles: server_default now() for created_at

The handle_new_user() trigger installed in migration 72f574ad4871 inserts into
public.profiles without supplying created_at, so new Supabase auth sign-ups
failed with a NOT NULL violation. Adding a SQL-side default fixes it.

Revision ID: a1b3c5e7f9d0
Revises: 8824b5f46018
Create Date: 2026-04-17 22:55:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "a1b3c5e7f9d0"
down_revision: Union[str, None] = "72f574ad4871"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "profiles",
        "created_at",
        existing_type=sa.DateTime(),
        server_default=sa.text("now()"),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "profiles",
        "created_at",
        existing_type=sa.DateTime(),
        server_default=None,
        existing_nullable=False,
    )
