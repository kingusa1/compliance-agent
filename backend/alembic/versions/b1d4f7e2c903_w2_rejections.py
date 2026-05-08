"""W2 watt-coverage — rejections table + audit log

Revision ID: b1d4f7e2c903
Revises: d10e5f3a8b91
Create Date: 2026-05-03 16:00:00.000000

Wave 2 of v3-watt-coverage harness: introduces the ``rejections`` workflow
table (Stage 4 of Watt's 41-step flow) plus an audit log for every status
transition / patch.

Schema differs across dialects:

  Postgres
    - 4 enum types: rejection_category / rejection_status /
      rejection_outcome / remediation_action.
    - ``rejections.deadline`` is a plain TIMESTAMPTZ. We tried
      ``GENERATED ALWAYS AS (rejected_at + INTERVAL '2 days') STORED``
      first, but ``timestamptz + interval`` is STABLE not IMMUTABLE in
      Postgres so generated-column creation rejects it. The route layer
      (``rejections_routes._compute_deadline``) populates the value on
      every insert / update — same behaviour as SQLite.

  SQLite (tests)
    - No enums; CHECK constraints on each enum column with the same
      vocabulary so bad values still fail at write-time.
    - ``deadline`` is a plain TIMESTAMP. The route layer (see
      ``rejections_routes._compute_deadline``) fills it in on every insert
      / update of ``rejected_at`` so reads behave identically to PG.

The ladder vocabulary (XLSX deep-dive §2.4-2.7):

    REJECTION_CATEGORIES = [
        'ADMIN_ERROR', 'PROCESS_FAILURE', 'VERBAL_SALES_ERROR',
        'COMPLIANCE_ISSUE', 'COMPLIANCE_ERROR', 'PRICING_ISSUE',
        'DOCUSIGN_ERROR', 'FAILED_CREDIT_CHECK',
    ]
    REJECTION_STATUSES = [
        'NOT_STARTED', 'IN_PROGRESS', 'FIXED', 'BATCHED_TO_PORTAL',
        'SUBMITTED_TO_PORTAL', 'FIXED_AND_APPROVED', 'DEAD',
    ]
    REJECTION_OUTCOMES = [
        'FIXED_AND_SUBMITTED', 'CUSTOMER_LOST', 'CANCELLED',
        'NOT_RECOVERABLE', 'RESIGNED_TO_OTHER_SUPPLIER',
    ]
    REMEDIATION_ACTIONS = [
        'AMENDMENT_CALL', 'CONFIRMATION_CALL', 'NEW_LOA',
        'NEW_DOCUSIGN', 'DD_MANDATE', 'RESELL_TO_OTHER_SUPPLIER',
        'PRICE_RECHECK', 'COT_CHANGE_OF_TENANCY',
        'CONTRACT_LENGTH_LIMIT', 'MANUAL_ADMIN_SUBMISSION',
    ]
"""
from alembic import op
import sqlalchemy as sa


revision = "b1d4f7e2c903"
down_revision = "d10e5f3a8b91"
branch_labels = None
depends_on = None


REJECTION_CATEGORIES = [
    "ADMIN_ERROR",
    "PROCESS_FAILURE",
    "VERBAL_SALES_ERROR",
    "COMPLIANCE_ISSUE",
    "COMPLIANCE_ERROR",
    "PRICING_ISSUE",
    "DOCUSIGN_ERROR",
    "FAILED_CREDIT_CHECK",
]
REJECTION_STATUSES = [
    "NOT_STARTED",
    "IN_PROGRESS",
    "FIXED",
    "BATCHED_TO_PORTAL",
    "SUBMITTED_TO_PORTAL",
    "FIXED_AND_APPROVED",
    "DEAD",
]
REJECTION_OUTCOMES = [
    "FIXED_AND_SUBMITTED",
    "CUSTOMER_LOST",
    "CANCELLED",
    "NOT_RECOVERABLE",
    "RESIGNED_TO_OTHER_SUPPLIER",
]
REMEDIATION_ACTIONS = [
    "AMENDMENT_CALL",
    "CONFIRMATION_CALL",
    "NEW_LOA",
    "NEW_DOCUSIGN",
    "DD_MANDATE",
    "RESELL_TO_OTHER_SUPPLIER",
    "PRICE_RECHECK",
    "COT_CHANGE_OF_TENANCY",
    "CONTRACT_LENGTH_LIMIT",
    "MANUAL_ADMIN_SUBMISSION",
]


def _check_in(col: str, values: list[str]) -> str:
    inner = ", ".join(f"'{v}'" for v in values)
    return f"{col} IN ({inner})"


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    if is_pg:
        # ── enum types ──────────────────────────────────────────────
        op.execute(
            "CREATE TYPE rejection_category AS ENUM ("
            + ", ".join(f"'{v}'" for v in REJECTION_CATEGORIES)
            + ")"
        )
        op.execute(
            "CREATE TYPE rejection_status AS ENUM ("
            + ", ".join(f"'{v}'" for v in REJECTION_STATUSES)
            + ")"
        )
        op.execute(
            "CREATE TYPE rejection_outcome AS ENUM ("
            + ", ".join(f"'{v}'" for v in REJECTION_OUTCOMES)
            + ")"
        )
        op.execute(
            "CREATE TYPE remediation_action AS ENUM ("
            + ", ".join(f"'{v}'" for v in REMEDIATION_ACTIONS)
            + ")"
        )

        # ── rejections ─────────────────────────────────────────────
        op.execute(
            """
            CREATE TABLE rejections (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                call_id TEXT REFERENCES calls(id) ON DELETE SET NULL,
                customer_slug TEXT,
                external_watt_site_id INTEGER,
                supplier TEXT,
                sales_agent TEXT,
                category rejection_category NOT NULL,
                rejection_reason TEXT NOT NULL,
                fix_required remediation_action,
                fix_assignee_id VARCHAR REFERENCES profiles(id) ON DELETE SET NULL,
                status rejection_status NOT NULL DEFAULT 'NOT_STARTED',
                outcome rejection_outcome,
                outcome_narrative TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                rejected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                deadline TIMESTAMPTZ,
                resolved_at TIMESTAMPTZ
            )
            """
        )
        op.create_index("idx_rejections_status", "rejections", ["status"])
        op.create_index("idx_rejections_category", "rejections", ["category"])
        op.create_index("idx_rejections_call_id", "rejections", ["call_id"])
        op.create_index("idx_rejections_customer_slug", "rejections", ["customer_slug"])
        op.create_index(
            "idx_rejections_external_watt_site_id",
            "rejections",
            ["external_watt_site_id"],
        )

        # ── rejection_audit_log ────────────────────────────────────
        op.execute(
            """
            CREATE TABLE rejection_audit_log (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                rejection_id UUID NOT NULL REFERENCES rejections(id) ON DELETE CASCADE,
                actor_id VARCHAR REFERENCES profiles(id) ON DELETE SET NULL,
                action TEXT,
                from_status TEXT,
                to_status TEXT,
                notes TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        op.create_index(
            "idx_rejection_audit_log_rejection_id",
            "rejection_audit_log",
            ["rejection_id"],
        )
    else:
        # ── SQLite ──────────────────────────────────────────────────
        op.create_table(
            "rejections",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column(
                "call_id",
                sa.String(),
                sa.ForeignKey("calls.id", ondelete="SET NULL"),
                nullable=True,
                index=True,
            ),
            sa.Column("customer_slug", sa.Text(), nullable=True, index=True),
            sa.Column("external_watt_site_id", sa.Integer(), nullable=True),
            sa.Column("supplier", sa.Text(), nullable=True),
            sa.Column("sales_agent", sa.Text(), nullable=True),
            sa.Column("category", sa.String(), nullable=False),
            sa.Column("rejection_reason", sa.Text(), nullable=False),
            sa.Column("fix_required", sa.String(), nullable=True),
            sa.Column(
                "fix_assignee_id",
                sa.String(),
                sa.ForeignKey("profiles.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "status",
                sa.String(),
                nullable=False,
                server_default="NOT_STARTED",
            ),
            sa.Column("outcome", sa.String(), nullable=True),
            sa.Column("outcome_narrative", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("rejected_at", sa.DateTime(), nullable=False),
            sa.Column("deadline", sa.DateTime(), nullable=True),
            sa.Column("resolved_at", sa.DateTime(), nullable=True),
            sa.CheckConstraint(
                _check_in("category", REJECTION_CATEGORIES),
                name="ck_rejections_category",
            ),
            sa.CheckConstraint(
                _check_in("status", REJECTION_STATUSES),
                name="ck_rejections_status",
            ),
            sa.CheckConstraint(
                "outcome IS NULL OR " + _check_in("outcome", REJECTION_OUTCOMES),
                name="ck_rejections_outcome",
            ),
            sa.CheckConstraint(
                "fix_required IS NULL OR "
                + _check_in("fix_required", REMEDIATION_ACTIONS),
                name="ck_rejections_fix_required",
            ),
        )
        op.create_index("idx_rejections_status", "rejections", ["status"])
        op.create_index("idx_rejections_category", "rejections", ["category"])

        op.create_table(
            "rejection_audit_log",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column(
                "rejection_id",
                sa.String(),
                sa.ForeignKey("rejections.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column(
                "actor_id",
                sa.String(),
                sa.ForeignKey("profiles.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("action", sa.Text(), nullable=True),
            sa.Column("from_status", sa.String(), nullable=True),
            sa.Column("to_status", sa.String(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )


def downgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    op.drop_table("rejection_audit_log")
    op.drop_table("rejections")
    if is_pg:
        op.execute("DROP TYPE IF EXISTS remediation_action")
        op.execute("DROP TYPE IF EXISTS rejection_outcome")
        op.execute("DROP TYPE IF EXISTS rejection_status")
        op.execute("DROP TYPE IF EXISTS rejection_category")
