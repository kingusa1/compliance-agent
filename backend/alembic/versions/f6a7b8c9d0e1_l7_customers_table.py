"""L7 intake — customers table + customer_deals.customer_id FK + backfill

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-04-30 02:30:00.000000

Promotes "customer" from the implicit-string entity it is in customer_deals
to a first-class row in its own table. Backfill walks distinct customer_name
values, slugifies, dedupes by appending -2/-3, inserts a customer row, then
updates customer_deals.customer_id. ``customer_name`` is retained on
customer_deals for backwards compatibility (read-only legacy field — new
inserts go through the FK).
"""
import re

from alembic import op
import sqlalchemy as sa


revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def _slugify(name: str) -> str:
    """Lowercase, replace whitespace + slash with dash, drop everything
    that isn't ascii alpha-num or dash. Matches the runtime helper in
    app.intake.supplier_canonical so keys stay stable across paths."""
    s = (name or "").lower().strip()
    s = re.sub(r"[\s/_]+", "-", s)
    s = re.sub(r"[^a-z0-9\-]", "", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "unknown"


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("legal_name", sa.Text(), nullable=False),
        sa.Column("trading_as", sa.Text(), nullable=True),
        sa.Column("dob", sa.Date(), nullable=True),
        sa.Column("company_number", sa.Text(), nullable=True),
        sa.Column("charity_number", sa.Text(), nullable=True),
        sa.Column("address_postcode", sa.Text(), nullable=True),
        sa.Column("business_type", sa.String(), nullable=True),
        sa.Column(
            "vulnerable_customer_flag",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("FALSE"),
        ),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.UniqueConstraint("slug", name="uq_customers_slug"),
        sa.CheckConstraint(
            "business_type IS NULL OR business_type IN "
            "('sole_trader','limited','partnership','charity')",
            name="ck_customers_business_type",
        ),
    )
    op.create_index("idx_customers_slug", "customers", ["slug"])

    # customer_deals.customer_id FK (nullable; legacy rows backfilled below).
    op.add_column(
        "customer_deals",
        sa.Column(
            "customer_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("customers.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index("idx_customer_deals_customer_id", "customer_deals", ["customer_id"])

    # ── Backfill: walk distinct customer_name values, dedupe slug, insert. ──
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT DISTINCT customer_name FROM customer_deals "
            "WHERE customer_name IS NOT NULL AND customer_name <> ''"
        )
    ).fetchall()

    used_slugs: set[str] = set()
    for (name,) in rows:
        base = _slugify(name)
        slug = base
        n = 2
        while slug in used_slugs:
            slug = f"{base}-{n}"
            n += 1
        used_slugs.add(slug)
        # Insert customer; capture id; then point all matching deals at it.
        result = bind.execute(
            sa.text(
                "INSERT INTO customers (legal_name, slug) "
                "VALUES (:name, :slug) RETURNING id"
            ),
            {"name": name, "slug": slug},
        )
        cid = result.scalar()
        bind.execute(
            sa.text(
                "UPDATE customer_deals SET customer_id = :cid "
                "WHERE customer_name = :name AND customer_id IS NULL"
            ),
            {"cid": cid, "name": name},
        )


def downgrade() -> None:
    op.drop_index("idx_customer_deals_customer_id", table_name="customer_deals")
    op.drop_column("customer_deals", "customer_id")
    op.drop_index("idx_customers_slug", table_name="customers")
    op.drop_table("customers")
