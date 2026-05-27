"""Wave-24c: Pozitive Preamble script + retire the 71-CP verbal script.

Owner reported 2026-05-27: "Pozitive doesn't have a verbal contract.
It's just a preamble. The AI should pick up that pass-through charges
are applied to the bill and it is not a fully fixed contract due to
these charges."

The existing live script ``Pozitive Verbal Contract Script (PE)``
(id ``1f7b102c-bac1-4475-9757-94449d0d3807``) has 71 verbal-contract
checkpoints (call recording, LOA, T&Cs, consent, ..., end-of-call).
None of them check for the pass-through-charges disclosure that's the
ACTUAL Pozitive compliance requirement. So every Pozitive call gets
graded against a rubric that doesn't match what's actually said —
mass false-fails on irrelevant checkpoints + zero coverage of the one
disclosure that matters.

This migration:
1. Inserts a NEW script ``Pozitive Preamble (Pass-Through Disclosure)``
   with 3 checkpoints targeting the actual Pozitive compliance content
   per owner's verbatim wording.
2. Marks the existing 71-CP ``Pozitive Verbal Contract Script (PE)``
   as inactive (``active=False``) so the script-matcher no longer
   selects it for new Pozitive calls. The row stays in the DB for
   historical reference + ability to re-activate if a true Pozitive
   verbal contract surfaces in the future.

Owner caveat (recorded in the wave-24 commit body): the 3 checkpoint
phrasings below are owner's best-guess from a single conversational
description. They should be refined against a real Pozitive transcript
in a follow-up wave.

Revision ID: 2026_05_27_pozitive_preamble
Revises: 2026_05_27_cp_needs_rev_idx
Create Date: 2026-05-27
"""
from __future__ import annotations

import json
import uuid

from alembic import op
from sqlalchemy import text


# Revision identifier. 28 chars — under the 32-char Postgres
# ``alembic_version.version_num`` ceiling per LAW_OF_ENTERPRISE_GRADE §1.
revision = "2026_05_27_pozitive_preamble"
down_revision = "2026_05_27_cp_needs_rev_idx"
branch_labels = None
depends_on = None


# Wave-24c — Pozitive Preamble checkpoints. 3 checkpoints targeting the
# pass-through-charges disclosure owner described verbatim. Format
# matches the existing Script.checkpoints JSON shape (see
# BRAIN/02_Domain/Scripts.md and supplier_seed.py).
POZITIVE_PREAMBLE_CHECKPOINTS = [
    {
        "section": 1,
        "name": "Identify Pozitive as the energy supplier",
        "required": (
            "Agent must clearly identify Pozitive (Energy) as the supplier "
            "for the customer's electricity supply being discussed."
        ),
        "key_phrases": [
            "pozitive",
            "pozitive energy",
            "supplier",
            "energy supplier",
        ],
        "customer_response_required": False,
        "strictness": "mandatory",
        "line_number": 1,
    },
    {
        "section": 2,
        "name": "Disclose pass-through charges on electric supply",
        "required": (
            "Agent must inform the customer that pass-through charges are "
            "applied to the bill (non-commodity costs charged at supplier "
            "cost, not fixed at contract sign). Required for electric "
            "supply contracts."
        ),
        "key_phrases": [
            "pass-through",
            "pass through",
            "passthrough",
            "non-commodity",
            "applied to the bill",
            "additional charges",
        ],
        "customer_response_required": False,
        "strictness": "mandatory",
        "line_number": 2,
    },
    {
        "section": 3,
        "name": "Disclose contract is not fully fixed due to these charges",
        "required": (
            "Agent must state the contract is NOT fully fixed because of "
            "the pass-through charges — the unit rate / standing charge "
            "fix doesn't cover the non-commodity component."
        ),
        "key_phrases": [
            "not fully fixed",
            "not a fully fixed",
            "not entirely fixed",
            "due to these charges",
            "because of pass-through",
            "not fixed",
        ],
        "customer_response_required": False,
        "strictness": "mandatory",
        "line_number": 3,
    },
]


# Existing live Pozitive Verbal Contract Script id (queried 2026-05-27).
# Confirmed via GET /api/scripts on prod: this is the only Pozitive
# script row in the live DB and it carries 71 verbal-contract
# checkpoints. The migration deactivates it; the row + checkpoints stay
# in the DB for forensic reference.
EXISTING_POZITIVE_VERBAL_SCRIPT_ID = "1f7b102c-bac1-4475-9757-94449d0d3807"


def upgrade() -> None:
    bind = op.get_bind()

    # Widen the ck_scripts_lifecycle_phase CHECK constraint to admit
    # 'preamble' as a valid lifecycle. The constraint was introduced in
    # c3d4e5f6a7b8_l3_deal_lifecycle_loa with a closed set
    # (lead_gen / closer / amendment / c_call / standalone_loa / passover
    # / full) that didn't anticipate Pozitive's NON-verbal pre-contract
    # disclosure. Without this drop+recreate the INSERT below fails on
    # Postgres with `new row for relation "scripts" violates check
    # constraint "ck_scripts_lifecycle_phase"` (CI run 26513437261).
    #
    # SQLite (CI alembic-on-sqlite + local tests) doesn't enforce CHECK
    # constraints the same way; the drop is a best-effort no-op there.
    if bind.dialect.name == "postgresql":
        # DO-block wraps ADD CONSTRAINT in an EXCEPTION handler so a
        # re-run (downgrade → upgrade) doesn't error on duplicate_object
        # when the widened constraint is already installed. Per
        # database-reviewer + python-reviewer trio 2026-05-27.
        bind.execute(text("""
            DO $$
            BEGIN
                ALTER TABLE scripts DROP CONSTRAINT IF EXISTS ck_scripts_lifecycle_phase;
                ALTER TABLE scripts ADD CONSTRAINT ck_scripts_lifecycle_phase CHECK (
                    lifecycle_phase IS NULL OR lifecycle_phase IN (
                        'lead_gen','closer','amendment','c_call','standalone_loa',
                        'passover','full','preamble'
                    )
                );
            EXCEPTION WHEN duplicate_object THEN
                NULL;  -- constraint already in place — safe re-run
            END $$;
        """))

    # Insert the new Preamble script. Idempotent: skip if one with the
    # same name already exists (re-runs on staging/local don't double-insert).
    existing = bind.execute(
        text(
            "SELECT id FROM scripts WHERE script_name = :name LIMIT 1"
        ),
        {"name": "Pozitive Preamble (Pass-Through Disclosure)"},
    ).fetchone()

    if not existing:
        new_id = str(uuid.uuid4())
        # SQLite (CI) tolerates the literal JSON via the Text column;
        # Postgres stores it as the same JSON text the model expects.
        bind.execute(
            text(
                """
                INSERT INTO scripts (
                    id, supplier_name, script_name, version, active,
                    lifecycle_phase, checkpoints, created_at
                ) VALUES (
                    :id, :supplier_name, :script_name, :version, :active,
                    :lifecycle_phase, :checkpoints, NOW()
                )
                """
                if bind.dialect.name == "postgresql"
                else """
                INSERT INTO scripts (
                    id, supplier_name, script_name, version, active,
                    lifecycle_phase, checkpoints, created_at
                ) VALUES (
                    :id, :supplier_name, :script_name, :version, :active,
                    :lifecycle_phase, :checkpoints, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "id": new_id,
                "supplier_name": "Pozitive",
                "script_name": "Pozitive Preamble (Pass-Through Disclosure)",
                "version": "v1",
                "active": True,
                "lifecycle_phase": "preamble",
                "checkpoints": json.dumps(POZITIVE_PREAMBLE_CHECKPOINTS),
            },
        )

    # Deactivate the existing 71-CP verbal contract script. The row
    # stays; only the active flag flips so the matcher stops picking it.
    # Idempotent: setting active=False on an already-inactive row is a
    # no-op flip.
    bind.execute(
        text(
            "UPDATE scripts SET active = :active "
            "WHERE id = :id AND active = TRUE"
        ),
        {"active": False, "id": EXISTING_POZITIVE_VERBAL_SCRIPT_ID},
    )


def downgrade() -> None:
    bind = op.get_bind()

    # Note: this downgrade is INTENTIONALLY asymmetric — it deletes the
    # data row but leaves ck_scripts_lifecycle_phase widened to include
    # 'preamble'. After the DELETE no row has lifecycle_phase='preamble'
    # so the widened constraint causes no data-integrity risk, and
    # narrowing the constraint here would (a) require validating every
    # remaining row against the old set and (b) re-break upgrade re-runs
    # on a downgrade-then-upgrade cycle. Per database-reviewer 2026-05-27.

    # Remove the Preamble script we inserted.
    bind.execute(
        text(
            "DELETE FROM scripts "
            "WHERE script_name = :name"
        ),
        {"name": "Pozitive Preamble (Pass-Through Disclosure)"},
    )

    # Re-activate the original Pozitive Verbal Contract Script.
    bind.execute(
        text(
            "UPDATE scripts SET active = :active WHERE id = :id"
        ),
        {"active": True, "id": EXISTING_POZITIVE_VERBAL_SCRIPT_ID},
    )
