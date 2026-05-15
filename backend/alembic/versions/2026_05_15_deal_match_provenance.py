"""deal-linker provenance — customer_deals.match_method + match_confidence

Adds two columns to ``customer_deals`` so the intake matcher
(``app.intake.matcher.find_existing_deal``) can record how a new call's
deal was resolved:

* ``match_method`` (string, nullable) — categorical tag, one of
  ``hard_key:mpan | hard_key:mprn | hard_key:docusign |
  hard_key:company_number | hard_key:charity_number |
  composite_auto | composite_review | reviewer_picked | legacy``.

* ``match_confidence`` (numeric, nullable) — calibrated posterior 0.0-1.0.
  ``1.0`` for hard keys; weighted-sum output for composite; null on rows
  created before the matcher landed.

Both columns are NULL on every existing row. The matcher writes them
only when it produces a match; the legacy upsert path may also stamp
``legacy`` + ``NULL`` confidence for audit clarity (future commit — not
required by this migration).

Revision ID: 2026_05_15_dealmatch
Revises: 2026_05_14_stagefix
Create Date: 2026-05-15
"""
from alembic import op
import sqlalchemy as sa


revision = "2026_05_15_dealmatch"
down_revision = "2026_05_14_stagefix"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "customer_deals",
        sa.Column("match_method", sa.String(), nullable=True),
    )
    op.add_column(
        "customer_deals",
        sa.Column("match_confidence", sa.Numeric(), nullable=True),
    )
    # Lightweight index so /admin/merges and ops queries on "deals matched
    # automatically vs by reviewer" stay snappy without a full scan.
    op.create_index(
        "ix_customer_deals_match_method",
        "customer_deals",
        ["match_method"],
    )


def downgrade() -> None:
    op.drop_index("ix_customer_deals_match_method", table_name="customer_deals")
    op.drop_column("customer_deals", "match_confidence")
    op.drop_column("customer_deals", "match_method")
