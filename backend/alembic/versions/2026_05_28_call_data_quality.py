"""add calls.data_quality_warnings JSONB (wave-50)

Revision ID: 2026_05_28_call_data_quality
Revises: 2026_05_27_backfill_script_mode
Create Date: 2026-05-28

Wave-50 — a non-compliance "data quality" channel on calls, used first
for the customer-name-mismatch warning (reviewer uploaded a recording
whose detected business name strongly diverges from the deal it was
attached to — i.e. likely the wrong customer's call).

Deliberately SEPARATE from the compliance ``flags`` table so a
data-quality signal never pollutes the compliance findings / report.
Shape: list[{code: str, message: str}].
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2026_05_28_call_data_quality"
down_revision: Union[str, None] = "2026_05_27_backfill_script_mode"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotent: prod may already have the column from a db.create_all
    # deploy; CI's fresh Postgres won't. JSONB default '[]' so existing
    # rows read back as an empty list, never NULL.
    op.execute(
        "ALTER TABLE calls ADD COLUMN IF NOT EXISTS "
        "data_quality_warnings JSONB NOT NULL DEFAULT '[]'::jsonb"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE calls DROP COLUMN IF EXISTS data_quality_warnings")
