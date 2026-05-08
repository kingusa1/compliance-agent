"""W1 watt-coverage — foundation backfill

Revision ID: d10e5f3a8b91
Revises: b8c9d0e1f2a3
Create Date: 2026-05-03 12:00:00.000000

Wave 1 of v3-watt-coverage harness. Five additive schema changes derived
from the Watt XLSX deep-dive (`.planning/v3-rebuild/2026-05-03-watt-xlsx-deep-dive.md`).
None drop columns or break existing readers.

  W1.1 — customers.external_watt_site_id, customer_deals.external_watt_site_id
         Watt portal deep-link integer (every rejection-tracker row has one).
  W1.2 — customer_deals.meters JSONB array
         Replaces single ``mpan_or_mprn`` for dual-fuel deals. Backfill walks
         existing rows and lifts ``mpan_or_mprn`` into a 1-element array.
         ``mpan_or_mprn`` is retained read-only.
  W1.4 — sales_agent_aliases table (canonical_name, alias)
         Empty seed; admin populates via Settings tab in W4.
  W1.5 — calls.risk_tags text[] (Postgres) / TEXT (SQLite)
         Per-call risk-tag chips
         (Ombudsman/Mis-selling/Complaint/Cancellation/Vulnerable).
  W1.6 — call_checkpoints.line_number INT
         Script-line reference ("amendment for line 11-14"). Backfill where
         scripts.checkpoints JSON has explicit line_number.

All migrations run in a single revision to keep the chain compact and
let the test DB bring everything up in one Base.metadata.create_all step.
"""
import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "d10e5f3a8b91"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # ── W1.1 ── external_watt_site_id on customers + customer_deals
    op.add_column(
        "customers",
        sa.Column("external_watt_site_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "idx_customers_external_watt_site_id",
        "customers",
        ["external_watt_site_id"],
    )
    op.add_column(
        "customer_deals",
        sa.Column("external_watt_site_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "idx_customer_deals_external_watt_site_id",
        "customer_deals",
        ["external_watt_site_id"],
    )

    # ── W1.2 ── meters JSONB array on customer_deals
    if is_pg:
        op.execute(
            "ALTER TABLE customer_deals "
            "ADD COLUMN meters JSONB NOT NULL DEFAULT '[]'::jsonb"
        )
    else:  # SQLite (tests)
        op.add_column(
            "customer_deals",
            sa.Column(
                "meters",
                sa.Text(),
                nullable=False,
                server_default="[]",
            ),
        )

    # Backfill: lift mpan_or_mprn → 1-element meters array. We can't tell
    # MPAN vs MPRN from the legacy string, so default to MPAN; the old
    # column stays read-only for callers that need the original string.
    if is_pg:
        op.execute(
            """
            UPDATE customer_deals
               SET meters = jsonb_build_array(jsonb_build_object('mpan', mpan_or_mprn))
             WHERE mpan_or_mprn IS NOT NULL
               AND mpan_or_mprn <> ''
               AND (meters = '[]'::jsonb OR meters IS NULL)
            """
        )
    else:
        rows = bind.execute(
            sa.text(
                "SELECT id, mpan_or_mprn FROM customer_deals "
                "WHERE mpan_or_mprn IS NOT NULL AND mpan_or_mprn <> ''"
            )
        ).fetchall()
        for r in rows:
            payload = json.dumps([{"mpan": r.mpan_or_mprn}])
            bind.execute(
                sa.text(
                    "UPDATE customer_deals SET meters = :payload WHERE id = :id"
                ),
                {"payload": payload, "id": str(r.id)},
            )

    # ── W1.4 ── sales_agent_aliases table
    if is_pg:
        op.create_table(
            "sales_agent_aliases",
            sa.Column(
                "id",
                sa.dialects.postgresql.UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("canonical_name", sa.Text(), nullable=False),
            sa.Column("alias", sa.Text(), nullable=False),
            sa.Column(
                "created_at",
                sa.TIMESTAMP(timezone=True),
                server_default=sa.text("NOW()"),
                nullable=False,
            ),
            sa.UniqueConstraint("alias", name="uq_sales_agent_aliases_alias"),
        )
    else:
        op.create_table(
            "sales_agent_aliases",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("canonical_name", sa.Text(), nullable=False),
            sa.Column("alias", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("alias", name="uq_sales_agent_aliases_alias"),
        )
    op.create_index(
        "idx_sales_agent_aliases_canonical",
        "sales_agent_aliases",
        ["canonical_name"],
    )

    # ── W1.5 ── calls.risk_tags
    if is_pg:
        # Avoid full-table rewrite on Supabase pooler — add nullable, no
        # default (instant DDL). App-side coerces NULL → [].
        op.execute("ALTER TABLE calls ADD COLUMN risk_tags TEXT[]")
    else:
        op.add_column(
            "calls",
            sa.Column(
                "risk_tags",
                sa.Text(),
                nullable=False,
                server_default="[]",
            ),
        )

    # ── W1.6 ── call_checkpoints.line_number + backfill
    op.add_column(
        "call_checkpoints",
        sa.Column("line_number", sa.Integer(), nullable=True),
    )

    # Backfill: walk scripts.checkpoints JSON looking for explicit
    # line_number on each checkpoint, then update matching call_checkpoints
    # rows by name. Best-effort; missing line_numbers stay NULL.
    inspector = inspect(bind)
    if "scripts" in inspector.get_table_names() and "call_checkpoints" in inspector.get_table_names():
        scripts = bind.execute(
            sa.text("SELECT id, checkpoints FROM scripts")
        ).fetchall()
        for s in scripts:
            try:
                defs = json.loads(s.checkpoints or "[]")
            except Exception:
                continue
            if not isinstance(defs, list):
                continue
            for cp in defs:
                if not isinstance(cp, dict):
                    continue
                ln = cp.get("line_number")
                name = (cp.get("name") or "").strip()
                if ln is None or not name:
                    continue
                # Match call_checkpoints whose call.script_id == s.id and
                # whose rule_text contains the checkpoint name. Postgres
                # has script_id on calls; SQLite test env does too.
                bind.execute(
                    sa.text(
                        "UPDATE call_checkpoints SET line_number = :ln "
                        "WHERE call_id IN ("
                        "  SELECT id FROM calls WHERE script_id = :sid"
                        ") AND rule_text LIKE :pat"
                    ),
                    {"ln": int(ln), "sid": str(s.id), "pat": f"%{name}%"},
                )


def downgrade() -> None:
    op.drop_column("call_checkpoints", "line_number")
    op.drop_column("calls", "risk_tags")

    op.drop_index("idx_sales_agent_aliases_canonical", table_name="sales_agent_aliases")
    op.drop_table("sales_agent_aliases")

    op.drop_column("customer_deals", "meters")

    op.drop_index(
        "idx_customer_deals_external_watt_site_id",
        table_name="customer_deals",
    )
    op.drop_column("customer_deals", "external_watt_site_id")
    op.drop_index(
        "idx_customers_external_watt_site_id",
        table_name="customers",
    )
    op.drop_column("customers", "external_watt_site_id")
