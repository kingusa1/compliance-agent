"""pgvector embedding on agent_learnings

Phase J Task 29 — semantic similarity search over anonymized learnings. Adds
a nullable `embedding vector(1536)` column plus an ivfflat cosine index so the
agent can find past human corrections by meaning rather than by exact
supplier+checkpoint match.

Requires the `vector` extension to already exist on the database (see
backend/migrations_sql/003_pgvector.sql — must be applied before upgrading).

Revision ID: 0d24da0a1b40
Revises: b72e9f4a1c8d
Create Date: 2026-04-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

try:
    from pgvector.sqlalchemy import Vector
except ImportError:  # pragma: no cover
    Vector = None  # type: ignore[assignment]


revision: str = "0d24da0a1b40"
down_revision: Union[str, None] = "b72e9f4a1c8d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotent extension (003_pgvector.sql already enabled it; this keeps
    # fresh environments self-contained if the SQL file is skipped).
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    op.add_column(
        "agent_learnings",
        sa.Column("embedding", Vector(1536), nullable=True),
    )
    # ivfflat with 100 lists is a reasonable default for <100k rows; tune via
    # ANALYZE once the table is populated. vector_cosine_ops matches the <=>
    # operator used in get_similar_learnings().
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_learnings_embedding "
        "ON agent_learnings USING ivfflat (embedding vector_cosine_ops) "
        "WITH (lists = 100);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_agent_learnings_embedding;")
    op.drop_column("agent_learnings", "embedding")
