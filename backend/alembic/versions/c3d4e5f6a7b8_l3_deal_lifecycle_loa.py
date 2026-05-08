"""L3 enterprise sprint — deal lifecycle + LOA + commission + script lifecycle_phase

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-04-30 02:00:00.000000

Adds Watt-aligned deal lifecycle tracking:
  - customer_deals.lifecycle_status (open|lead_gen_done|closer_done|c_call_done|amendment_done|verified|rejected)
  - customer_deals.loa_status (bundled|standalone_call|document_attached|missing)
  - customer_deals.loa_document_url
  - customer_deals.mpan_electricity / mprn_gas (replace single mpan_or_mprn over time)
  - customer_deals.commission_value + commission_unit (pct|gbp)
  - customer_deals.term_months (12|24|36|48|60)
  - customer_deals.docusign_reference

  - scripts.lifecycle_phase (lead_gen|closer|amendment|c_call|standalone_loa|passover|full)
    Backfilled by name pattern.
"""
from alembic import op
import sqlalchemy as sa


revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── customer_deals: lifecycle + LOA ────────────────────────────────
    op.add_column(
        "customer_deals",
        sa.Column(
            "lifecycle_status",
            sa.String(),
            nullable=False,
            server_default="open",
        ),
    )
    op.create_check_constraint(
        "ck_customer_deals_lifecycle_status",
        "customer_deals",
        "lifecycle_status IN ('open','lead_gen_done','closer_done',"
        "'c_call_done','amendment_done','verified','rejected')",
    )

    op.add_column(
        "customer_deals",
        sa.Column("loa_status", sa.String(), nullable=True, server_default="missing"),
    )
    op.create_check_constraint(
        "ck_customer_deals_loa_status",
        "customer_deals",
        "loa_status IS NULL OR loa_status IN "
        "('bundled','standalone_call','document_attached','missing')",
    )

    op.add_column("customer_deals", sa.Column("loa_document_url", sa.Text(), nullable=True))
    op.add_column("customer_deals", sa.Column("mpan_electricity", sa.Text(), nullable=True))
    op.add_column("customer_deals", sa.Column("mprn_gas", sa.Text(), nullable=True))
    op.add_column("customer_deals", sa.Column("commission_value", sa.Numeric(), nullable=True))
    op.add_column("customer_deals", sa.Column("commission_unit", sa.String(), nullable=True))
    op.create_check_constraint(
        "ck_customer_deals_commission_unit",
        "customer_deals",
        "commission_unit IS NULL OR commission_unit IN ('pct','gbp')",
    )
    op.add_column("customer_deals", sa.Column("term_months", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_customer_deals_term_months",
        "customer_deals",
        "term_months IS NULL OR term_months IN (12,24,36,48,60)",
    )
    op.add_column("customer_deals", sa.Column("docusign_reference", sa.Text(), nullable=True))

    # ── scripts: lifecycle_phase ───────────────────────────────────────
    op.add_column("scripts", sa.Column("lifecycle_phase", sa.String(), nullable=True))
    op.create_check_constraint(
        "ck_scripts_lifecycle_phase",
        "scripts",
        "lifecycle_phase IS NULL OR lifecycle_phase IN "
        "('lead_gen','closer','amendment','c_call','standalone_loa','passover','full')",
    )

    # Backfill scripts.lifecycle_phase from script_name patterns. Uses
    # case-insensitive ILIKE so any combination of casing matches.
    op.execute("""
        UPDATE scripts SET lifecycle_phase = 'lead_gen'
        WHERE lifecycle_phase IS NULL AND script_name ILIKE '%lead%gen%'
    """)
    op.execute("""
        UPDATE scripts SET lifecycle_phase = 'closer'
        WHERE lifecycle_phase IS NULL AND script_name ILIKE '%closer%'
    """)
    op.execute("""
        UPDATE scripts SET lifecycle_phase = 'amendment'
        WHERE lifecycle_phase IS NULL AND script_name ILIKE '%amend%'
    """)
    op.execute("""
        UPDATE scripts SET lifecycle_phase = 'c_call'
        WHERE lifecycle_phase IS NULL AND (script_name ILIKE '%c-call%' OR script_name ILIKE '%c call%' OR script_name ILIKE '%verification%')
    """)
    op.execute("""
        UPDATE scripts SET lifecycle_phase = 'standalone_loa'
        WHERE lifecycle_phase IS NULL AND (script_name ILIKE '%loa%' OR script_name ILIKE '%standalone%')
    """)
    op.execute("""
        UPDATE scripts SET lifecycle_phase = 'passover'
        WHERE lifecycle_phase IS NULL AND script_name ILIKE '%passover%'
    """)


def downgrade() -> None:
    # scripts
    op.drop_constraint("ck_scripts_lifecycle_phase", "scripts", type_="check")
    op.drop_column("scripts", "lifecycle_phase")

    # customer_deals
    op.drop_column("customer_deals", "docusign_reference")
    op.drop_constraint("ck_customer_deals_term_months", "customer_deals", type_="check")
    op.drop_column("customer_deals", "term_months")
    op.drop_constraint("ck_customer_deals_commission_unit", "customer_deals", type_="check")
    op.drop_column("customer_deals", "commission_unit")
    op.drop_column("customer_deals", "commission_value")
    op.drop_column("customer_deals", "mprn_gas")
    op.drop_column("customer_deals", "mpan_electricity")
    op.drop_column("customer_deals", "loa_document_url")
    op.drop_constraint("ck_customer_deals_loa_status", "customer_deals", type_="check")
    op.drop_column("customer_deals", "loa_status")
    op.drop_constraint("ck_customer_deals_lifecycle_status", "customer_deals", type_="check")
    op.drop_column("customer_deals", "lifecycle_status")
