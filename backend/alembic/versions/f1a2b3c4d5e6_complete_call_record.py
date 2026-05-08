"""complete call record — capture full provider responses + human-friendly ids

Adds:
- call_ref          CA-2026-0001 human-readable sequence
- slug              URL-safe filename segment
- deepgram_metadata full Deepgram response (sentiment, intents, topics, summary, confidence)
- assemblyai_metadata full AssemblyAI response (speakers, chapters, entities)
- openai_whisper_metadata full Whisper validation response
- processing_log    ordered per-stage timing + byte counts
- raw_llm_io        per-LLM-call tokens + prompt hash + duration

Plus indexes:
- call_ref, slug unique B-tree
- filename B-tree prefix (pg_trgm not available on the Supabase pooler;
  fuzzy search uses ILIKE instead)

Revision ID: f1a2b3c4d5e6
Revises: e5a7c9d1f234
Create Date: 2026-04-19 02:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "f1a2b3c4d5e6"
down_revision = "e5a7c9d1f234"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Supabase pooler applies a tight statement_timeout; relax for this session.
    op.execute("SET statement_timeout = 0")

    op.add_column("calls", sa.Column("call_ref", sa.String(), nullable=True))
    op.add_column("calls", sa.Column("slug", sa.String(), nullable=True))

    op.add_column(
        "calls",
        sa.Column("deepgram_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "calls",
        sa.Column("assemblyai_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "calls",
        sa.Column("openai_whisper_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "calls",
        sa.Column("processing_log", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "calls",
        sa.Column("raw_llm_io", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    op.create_index("ix_calls_call_ref", "calls", ["call_ref"], unique=True)
    op.create_index("ix_calls_slug", "calls", ["slug"], unique=True)
    op.create_index("ix_calls_filename", "calls", ["filename"])


def downgrade() -> None:
    op.drop_index("ix_calls_filename", table_name="calls")
    op.drop_index("ix_calls_slug", table_name="calls")
    op.drop_index("ix_calls_call_ref", table_name="calls")

    op.drop_column("calls", "raw_llm_io")
    op.drop_column("calls", "processing_log")
    op.drop_column("calls", "openai_whisper_metadata")
    op.drop_column("calls", "assemblyai_metadata")
    op.drop_column("calls", "deepgram_metadata")
    op.drop_column("calls", "slug")
    op.drop_column("calls", "call_ref")
