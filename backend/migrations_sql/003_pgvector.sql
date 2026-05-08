-- Phase J Task 29 — enable pgvector for semantic search on agent_learnings.
--
-- pgvector 0.8.0 is bundled with Supabase (AVAILABLE but not enabled). Enabling
-- the extension unlocks the `vector` type + cosine-distance operator (<=>).
-- The embedding column + ivfflat index are added by an Alembic migration that
-- depends on this extension already existing on the target database.
--
-- Run once per environment:
--   psql "$MIGRATION_DATABASE_URL" -f backend/migrations_sql/003_pgvector.sql
CREATE EXTENSION IF NOT EXISTS vector;
