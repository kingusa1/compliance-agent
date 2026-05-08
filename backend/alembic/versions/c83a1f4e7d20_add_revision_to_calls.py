"""add revision to calls

Phase J Task 33 — optimistic locking on Call mutations. A monotonically
increasing counter bumped by every mutating HITL endpoint (claim, release,
verdict, edit-word, compliance). Callers can send `If-Match: <revision>` on
mutate; mismatch returns 409 so a reviewer's save never silently overwrites
a concurrent edit (e.g. a lead reopening the call mid-submit).

Drafts intentionally skip the bump — autosave every 10s would cause spurious
409 storms — so we backfill existing rows with `1` and let the first real
mutation own the bump.

Revision ID: c83a1f4e7d20
Revises: 4d5d09ce7455
Create Date: 2026-04-17 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c83a1f4e7d20"
down_revision: Union[str, None] = "4d5d09ce7455"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default="1" so existing rows pick up a sane starting value without
    # a separate UPDATE. The ORM-side default=1 covers new inserts.
    op.add_column(
        "calls",
        sa.Column(
            "revision",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )


def downgrade() -> None:
    op.drop_column("calls", "revision")
