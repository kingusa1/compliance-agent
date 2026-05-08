"""L10 RAG — 5 new namespace chunk tables (LOA + supplier_docs + gates + rules + rejections)

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-04-30 03:00:00.000000

Five pgvector-backed chunk tables expanding L6 RAG from 2 namespaces
(transcripts, scripts) to 7. Each table mirrors the L6 pattern:
ivfflat cosine indexes for fast top-k, ``embedding`` nullable so a
failed embed call doesn't block row insertion.
"""
from alembic import op
import sqlalchemy as sa


revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ── loa_chunks ─────────────────────────────────────────────────────
    op.create_table(
        "loa_chunks",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("supplier", sa.String(), nullable=False),
        sa.Column("chunk_idx", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("section", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
    )
    op.execute("ALTER TABLE loa_chunks ADD COLUMN embedding vector(1536)")
    op.create_index("ix_loa_chunks_supplier", "loa_chunks", ["supplier"])
    op.execute(
        "CREATE INDEX ix_loa_chunks_embedding "
        "ON loa_chunks USING ivfflat (embedding vector_cosine_ops) "
        "WITH (lists = 100)"
    )

    # ── supplier_doc_chunks ────────────────────────────────────────────
    op.create_table(
        "supplier_doc_chunks",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("supplier", sa.String(), nullable=False),
        sa.Column("doc_type", sa.String(), nullable=False),
        sa.Column("chunk_idx", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("section", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
    )
    op.execute("ALTER TABLE supplier_doc_chunks ADD COLUMN embedding vector(1536)")
    op.create_index(
        "ix_supplier_doc_chunks_lookup",
        "supplier_doc_chunks",
        ["supplier", "doc_type"],
    )
    op.execute(
        "CREATE INDEX ix_supplier_doc_chunks_embedding "
        "ON supplier_doc_chunks USING ivfflat (embedding vector_cosine_ops) "
        "WITH (lists = 100)"
    )

    # ── gate_chunks ────────────────────────────────────────────────────
    op.create_table(
        "gate_chunks",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("step_number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column(
            "chunk_idx",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
    )
    op.execute("ALTER TABLE gate_chunks ADD COLUMN embedding vector(1536)")
    op.create_index("ix_gate_chunks_step", "gate_chunks", ["step_number"])
    op.execute(
        "CREATE INDEX ix_gate_chunks_embedding "
        "ON gate_chunks USING ivfflat (embedding vector_cosine_ops) "
        "WITH (lists = 100)"
    )

    # ── rule_chunks ────────────────────────────────────────────────────
    op.create_table(
        "rule_chunks",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("rule_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("severity", sa.String(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
    )
    op.execute("ALTER TABLE rule_chunks ADD COLUMN embedding vector(1536)")
    op.create_index("ix_rule_chunks_rule_id", "rule_chunks", ["rule_id"])
    op.execute(
        "CREATE INDEX ix_rule_chunks_embedding "
        "ON rule_chunks USING ivfflat (embedding vector_cosine_ops) "
        "WITH (lists = 100)"
    )

    # ── rejection_chunks ───────────────────────────────────────────────
    op.create_table(
        "rejection_chunks",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("agent_name", sa.String(), nullable=True),
        sa.Column("supplier", sa.String(), nullable=True),
        sa.Column("fix", sa.String(), nullable=True),
        sa.Column("chunk_idx", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
    )
    op.execute("ALTER TABLE rejection_chunks ADD COLUMN embedding vector(1536)")
    op.create_index("ix_rejection_chunks_category", "rejection_chunks", ["category"])
    op.create_index("ix_rejection_chunks_supplier", "rejection_chunks", ["supplier"])
    op.execute(
        "CREATE INDEX ix_rejection_chunks_embedding "
        "ON rejection_chunks USING ivfflat (embedding vector_cosine_ops) "
        "WITH (lists = 100)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_rejection_chunks_embedding")
    op.drop_index("ix_rejection_chunks_supplier", table_name="rejection_chunks")
    op.drop_index("ix_rejection_chunks_category", table_name="rejection_chunks")
    op.drop_table("rejection_chunks")

    op.execute("DROP INDEX IF EXISTS ix_rule_chunks_embedding")
    op.drop_index("ix_rule_chunks_rule_id", table_name="rule_chunks")
    op.drop_table("rule_chunks")

    op.execute("DROP INDEX IF EXISTS ix_gate_chunks_embedding")
    op.drop_index("ix_gate_chunks_step", table_name="gate_chunks")
    op.drop_table("gate_chunks")

    op.execute("DROP INDEX IF EXISTS ix_supplier_doc_chunks_embedding")
    op.drop_index("ix_supplier_doc_chunks_lookup", table_name="supplier_doc_chunks")
    op.drop_table("supplier_doc_chunks")

    op.execute("DROP INDEX IF EXISTS ix_loa_chunks_embedding")
    op.drop_index("ix_loa_chunks_supplier", table_name="loa_chunks")
    op.drop_table("loa_chunks")
