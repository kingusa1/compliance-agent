"""W4.2 + W4.3 — script_line_mappings table + 15-row seed

Revision ID: c2e5g8h3i4j5
Revises: b1d4f7e2c903
Create Date: 2026-05-04 00:00:00.000000

Wave 4.2 of v3-watt-coverage harness. Introduces a small reference table
mapping (supplier, script_section, line_number) → canonical checkpoint
internal_key, joined by ``GET /api/scripts/{id}/lines`` to overlay
"[L17] prices EXCLUDE VAT" badges on the script viewer.

Seed (15 rows) is the §8 mapping table extracted from
``.planning/v3-rebuild/2026-05-03-watt-xlsx-deep-dive.md``. All rows are
E.ON-supplier today (only supplier whose script is line-numbered in the
rejection list); other suppliers can be appended in a later migration.

Idempotence:
  - Postgres branch uses ``CREATE TABLE IF NOT EXISTS``.
  - Index creation guarded by ``CREATE INDEX IF NOT EXISTS``.
  - Seed insert uses ``ON CONFLICT (internal_key) DO NOTHING`` so reruns
    are safe.
  - SQLite branch uses ``op.create_table`` (test scope only — fresh
    in-memory DB on every test).
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "c2e5g8h3i4j5"
down_revision = "b1d4f7e2c903"
branch_labels = None
depends_on = None


# ─── Seed data ──────────────────────────────────────────────────────────
# Source: .planning/v3-rebuild/2026-05-03-watt-xlsx-deep-dive.md §8
# Each tuple: (supplier, script_section, line_number, checkpoint_name, internal_key)
SEED_ROWS: list[tuple[str, str, int | None, str, str]] = [
    ("E.ON", "LOA opening", None,
     "Watt identity disclosure",
     "watt_identity_disclosure"),
    ("E.ON", "LOA",          None,
     "Broker independence disclaimer (no direct E.ON Next agreement)",
     "broker_independence_disclaimer"),
    # §8 row 3 ("amendment for lines 12, 13, 14") is a single rejection
    # narrative covering three EON-Verbal lines. Modeled as one record
    # keyed on line 12 (the anchor) so the seed row-count matches §8 row
    # count exactly (15). UI may surface the 12/13/14 range from
    # checkpoint_name; rejection text already cites all three lines.
    ("E.ON", "EON Verbal",   12,
     "Amendment-required: estimated cost / commission / prices (lines 12-14)",
     "eon_verbal_l12_14_amendment"),
    ("E.ON", "LOA",          5,
     "Decision-maker for site address confirmed",
     "loa_l5_decision_maker_confirmed"),
    ("E.ON", "EON Verbal",   11,
     "Term length and consumption confirmed",
     "eon_verbal_l11_term_consumption"),
    ("E.ON", "EON Verbal",   17,
     "Prices EXCLUDE VAT, CCL, Green Deal",
     "eon_verbal_l17_vat_ccl_disclosure"),
    ("E.ON", "EON Verbal",   20,
     "Microbusiness / Small Business consumer status",
     "eon_verbal_l20_microbusiness_status"),
    ("E.ON", "LOA",          None,
     "Customer-stated company name matches E.ON portal",
     "loa_company_name_match_portal"),
    ("E.ON", "LOA",          None,
     "Charity number confirmation in LOA",
     "loa_charity_number_confirmation"),
    ("E.ON", "LOA",          None,
     "Company number confirmation in LOA",
     "loa_company_number_confirmation"),
    ("E.ON", "LOA",          None,
     "Date of birth confirmation in LOA",
     "loa_dob_confirmation"),
    ("E.ON", "LOA",          None,
     "Third-party industry-database authorisation",
     "loa_industry_database_authority"),
    ("E.ON", "Verbal",       None,
     "No guaranteed-savings claim (rates fixed-for-N-years)",
     "verbal_no_guaranteed_savings"),
    # §8 row 14 narrates the same VAT/CCL/Green-Deal disclosure as
    # eon_verbal_l17_vat_ccl_disclosure, but from the (non-EON) "Verbal"
    # script flavour — kept distinct because rejection-narrative joins
    # need to find it via supplier="E.ON", script_section="Verbal".
    ("E.ON", "Verbal",       17,
     "Prices INCLUDE VAT, CCL, Green Deal (Verbal flavour)",
     "verbal_l17_vat_ccl_disclosure"),
    ("E.ON", "Verbal",       14,
     "Uplift pricing matches verbal quote",
     "verbal_uplift_pricing_match"),
]


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    if is_pg:
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS script_line_mappings (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                supplier TEXT NOT NULL,
                script_section TEXT NOT NULL,
                line_number INTEGER NULL,
                checkpoint_name TEXT NOT NULL,
                internal_key TEXT NOT NULL UNIQUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_script_line_mappings_supplier_section "
            "ON script_line_mappings (supplier, script_section)"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_script_line_mappings_internal_key "
            "ON script_line_mappings (internal_key)"
        )

        # Seed via parameterized executemany so quoting is bullet-proof.
        bind.execute(
            sa.text(
                """
                INSERT INTO script_line_mappings
                    (supplier, script_section, line_number, checkpoint_name, internal_key)
                VALUES
                    (:supplier, :script_section, :line_number, :checkpoint_name, :internal_key)
                ON CONFLICT (internal_key) DO NOTHING
                """
            ),
            [
                {
                    "supplier": s,
                    "script_section": sec,
                    "line_number": ln,
                    "checkpoint_name": cn,
                    "internal_key": ik,
                }
                for (s, sec, ln, cn, ik) in SEED_ROWS
            ],
        )
    else:
        # SQLite (test scope)
        op.create_table(
            "script_line_mappings",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("supplier", sa.Text(), nullable=False),
            sa.Column("script_section", sa.Text(), nullable=False),
            sa.Column("line_number", sa.Integer(), nullable=True),
            sa.Column("checkpoint_name", sa.Text(), nullable=False),
            sa.Column("internal_key", sa.Text(), nullable=False, unique=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index(
            "idx_script_line_mappings_supplier_section",
            "script_line_mappings",
            ["supplier", "script_section"],
        )
        op.create_index(
            "idx_script_line_mappings_internal_key",
            "script_line_mappings",
            ["internal_key"],
        )

        import uuid as _uuid
        from datetime import datetime as _dt

        bind.execute(
            sa.text(
                """
                INSERT OR IGNORE INTO script_line_mappings
                    (id, supplier, script_section, line_number, checkpoint_name, internal_key, created_at)
                VALUES
                    (:id, :supplier, :script_section, :line_number, :checkpoint_name, :internal_key, :created_at)
                """
            ),
            [
                {
                    "id": str(_uuid.uuid4()),
                    "supplier": s,
                    "script_section": sec,
                    "line_number": ln,
                    "checkpoint_name": cn,
                    "internal_key": ik,
                    "created_at": _dt.utcnow(),
                }
                for (s, sec, ln, cn, ik) in SEED_ROWS
            ],
        )


def downgrade() -> None:
    op.drop_table("script_line_mappings")
