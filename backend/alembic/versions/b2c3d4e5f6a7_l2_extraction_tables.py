"""L2 enterprise sprint — call_segments + flags + extracted_entities

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-04-30 01:05:00.000000

Three tables for the data extraction layer:
  - call_segments: one row per detected stage (Watt's 6-stage taxonomy)
  - flags: one row per failed/needs-review checkpoint, with severity + risk_tag
  - extracted_entities: one row per (call_id, key) pair, UNIQUE constraint

CHECK constraints enforce Watt's stage vocabulary + severity tiers + risk-tag
options at the DB layer so bad data can't enter even via raw SQL.
"""
from alembic import op
import sqlalchemy as sa


revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # call_segments
    op.create_table(
        "call_segments",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("call_id", sa.String(), sa.ForeignKey("calls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(), nullable=False),
        sa.Column("transcript_excerpt", sa.Text(), nullable=True),
        sa.Column("speaker", sa.String(), nullable=True),
        sa.Column("start_s", sa.Numeric(), nullable=True),
        sa.Column("end_s", sa.Numeric(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.CheckConstraint(
            "stage IN ('intro','qualification','pitch','transfer','verbal','close')",
            name="ck_call_segments_stage",
        ),
    )
    op.create_index("idx_call_segments_call_id", "call_segments", ["call_id"])

    # flags
    op.create_table(
        "flags",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("call_id", sa.String(), sa.ForeignKey("calls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("segment_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("call_segments.id", ondelete="SET NULL"), nullable=True),
        sa.Column("rule_id", sa.String(), nullable=False),
        sa.Column("severity", sa.String(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("word_start", sa.Integer(), nullable=True),
        sa.Column("word_end", sa.Integer(), nullable=True),
        sa.Column("risk_tag", sa.String(), nullable=True),
        sa.Column("source", sa.String(), nullable=False, server_default="auto"),
        sa.Column("created_by_id", sa.String(), sa.ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.CheckConstraint("severity IN ('critical','high','medium')", name="ck_flags_severity"),
        sa.CheckConstraint("source IN ('auto','reviewer')", name="ck_flags_source"),
        sa.CheckConstraint(
            "risk_tag IS NULL OR risk_tag IN ('ombudsman','mis-selling','complaint','cancellation')",
            name="ck_flags_risk_tag",
        ),
    )
    op.create_index("idx_flags_call_id", "flags", ["call_id"])
    op.create_index("idx_flags_segment_id", "flags", ["segment_id"])
    op.create_index("idx_flags_severity_risk", "flags", ["severity", "risk_tag"])

    # extracted_entities
    op.create_table(
        "extracted_entities",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("call_id", sa.String(), sa.ForeignKey("calls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(), nullable=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.CheckConstraint(
            "key IN ('mpan','mprn','deal_value_gbp','expected_live_date','commission','annual_cost','other')",
            name="ck_extracted_entities_key",
        ),
        sa.CheckConstraint("source IN ('regex','llm','word_match')", name="ck_extracted_entities_source"),
        sa.UniqueConstraint("call_id", "key", name="uq_extracted_entities_call_key"),
    )
    op.create_index("idx_extracted_entities_call_id", "extracted_entities", ["call_id"])


def downgrade() -> None:
    op.drop_index("idx_extracted_entities_call_id", table_name="extracted_entities")
    op.drop_table("extracted_entities")
    op.drop_index("idx_flags_severity_risk", table_name="flags")
    op.drop_index("idx_flags_segment_id", table_name="flags")
    op.drop_index("idx_flags_call_id", table_name="flags")
    op.drop_table("flags")
    op.drop_index("idx_call_segments_call_id", table_name="call_segments")
    op.drop_table("call_segments")
