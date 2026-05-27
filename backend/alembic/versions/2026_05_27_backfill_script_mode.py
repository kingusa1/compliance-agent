"""Backfill scripts.mode = 'meaning_for_meaning' where NULL.

Wave-24c regression hot-fix (2026-05-27 14:57 UTC). The wave-24c
migration ``2026_05_27_pozitive_preamble`` inserted the new Pozitive
Preamble row via raw SQL without the ``mode`` column. SQLAlchemy's
ORM default (``Column(String, default="meaning_for_meaning")``) does
not fire for raw INSERTs, so the row landed with ``mode IS NULL``.

The Pydantic ``ScriptResponse`` schema requires ``mode: str``, so
``GET /api/scripts`` started 500'ing on
``pydantic_core._pydantic_core.ValidationError: scripts.0.mode``
(seen repeatedly on Railway prod). The Scripts page + every UI
consumer of /api/scripts has been broken since wave-24c landed.

This migration:
  1. Updates every row where mode IS NULL to 'meaning_for_meaning'
     (the only legal default — see app.models.Script.mode default).
  2. Companion Pydantic-schema fix in app/schemas.py:ScriptResponse
     coerces None → default so any future raw-SQL slipup cannot take
     down the list endpoint.

Idempotent: re-running the UPDATE on rows that already have non-NULL
mode is a no-op.

Revision ID: 2026_05_27_backfill_script_mode
Revises: 2026_05_27_pozitive_preamble
Create Date: 2026-05-27
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text


# Revision identifier. 31 chars — under the 32-char Postgres
# alembic_version.version_num ceiling per LAW_OF_ENTERPRISE_GRADE §1.
revision = "2026_05_27_backfill_script_mode"
down_revision = "2026_05_27_pozitive_preamble"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(text(
        "UPDATE scripts SET mode = 'meaning_for_meaning' WHERE mode IS NULL"
    ))


def downgrade() -> None:
    # Intentional no-op. We cannot reliably distinguish rows that
    # originally had mode=NULL from rows that always had the default,
    # and setting mode back to NULL would re-break /api/scripts.
    pass
