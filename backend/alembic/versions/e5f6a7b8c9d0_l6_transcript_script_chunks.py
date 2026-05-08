"""L6 RAG — transcript_chunks + script_chunks (pgvector)

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-04-30 02:20:00.000000

Two pgvector-backed chunk tables for RAG retrieval. ivfflat indexes
with cosine distance for fast top-k. ``embedding`` is nullable so a
failed embed call (OpenAI rate-limit etc.) doesn't block the row.
"""
from alembic import op
import sqlalchemy as sa


revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # transcript_chunks
    op.create_table(
        "transcript_chunks",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "call_id",
            sa.String(),
            sa.ForeignKey("calls.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_idx", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("speaker", sa.String(), nullable=True),
        sa.Column("start_s", sa.Numeric(), nullable=True),
        sa.Column("end_s", sa.Numeric(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.UniqueConstraint("call_id", "chunk_idx", name="uq_transcript_chunks_call_idx"),
    )
    # embedding column added separately so we can use vector(1536) without
    # SQLAlchemy needing pgvector type metadata at op.create_table time.
    op.execute("ALTER TABLE transcript_chunks ADD COLUMN embedding vector(1536)")
    op.create_index("idx_transcript_chunks_call_id", "transcript_chunks", ["call_id"])
    op.execute(
        "CREATE INDEX ix_transcript_chunks_embedding "
        "ON transcript_chunks USING ivfflat (embedding vector_cosine_ops) "
        "WITH (lists = 100)"
    )

    # script_chunks
    op.create_table(
        "script_chunks",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "script_id",
            sa.String(),
            sa.ForeignKey("scripts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "script_version_id",
            sa.String(),
            sa.ForeignKey("script_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("checkpoint_idx", sa.Integer(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "script_version_id", "checkpoint_idx", name="uq_script_chunks_version_checkpoint"
        ),
    )
    op.execute("ALTER TABLE script_chunks ADD COLUMN embedding vector(1536)")
    op.create_index("idx_script_chunks_script_id", "script_chunks", ["script_id"])
    op.execute(
        "CREATE INDEX ix_script_chunks_embedding "
        "ON script_chunks USING ivfflat (embedding vector_cosine_ops) "
        "WITH (lists = 100)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_script_chunks_embedding")
    op.drop_index("idx_script_chunks_script_id", table_name="script_chunks")
    op.drop_table("script_chunks")
    op.execute("DROP INDEX IF EXISTS ix_transcript_chunks_embedding")
    op.drop_index("idx_transcript_chunks_call_id", table_name="transcript_chunks")
    op.drop_table("transcript_chunks")
