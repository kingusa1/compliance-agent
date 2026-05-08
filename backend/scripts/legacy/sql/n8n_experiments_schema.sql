-- N01 — n8n pipeline mirror sandbox schema
-- Target: amina-self-hosted Supabase (supabase_db_Compliance-Agent)
-- Schema: n8n_experiments (created 2026-04-28)
-- This file is idempotent — safe to re-run.

CREATE SCHEMA IF NOT EXISTS n8n_experiments;
GRANT USAGE ON SCHEMA n8n_experiments TO anon, authenticated, service_role;

-- runs: one row per webhook trigger / pipeline execution
CREATE TABLE IF NOT EXISTS n8n_experiments.runs (
  run_id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  call_id          uuid NULL,                    -- nullable; set if mirror is replaying an existing Inngest call
  customer_name    text NOT NULL,
  deal_id          text NULL,
  call_type        text NULL,                    -- lead_gen | closer | amendment | c_call | standalone_loa | full
  audio_url        text NOT NULL,
  status           text NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running','completed','failed','cancelled')),
  score            text NULL,                    -- fraction "p/total" mirroring backend's varchar score
  reason           text NULL,
  started_at       timestamptz NOT NULL DEFAULT now(),
  finished_at      timestamptz NULL,
  duration_ms      integer NULL,
  source           text NOT NULL DEFAULT 'n8n-mirror',
  metadata         jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_runs_started_at ON n8n_experiments.runs (started_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_customer   ON n8n_experiments.runs (customer_name);
CREATE INDEX IF NOT EXISTS idx_runs_status     ON n8n_experiments.runs (status);

-- steps: per-step waterfall data, mirrors Inngest's step shape
CREATE TABLE IF NOT EXISTS n8n_experiments.steps (
  step_id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id          uuid NOT NULL REFERENCES n8n_experiments.runs(run_id) ON DELETE CASCADE,
  step_name       text NOT NULL,                 -- download_audio | transcribe | detect_metadata | analyze_checkpoints | score | finalize
  step_index      integer NOT NULL,              -- 1..6
  status          text NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running','ok','err','skipped')),
  started_at      timestamptz NOT NULL DEFAULT now(),
  finished_at     timestamptz NULL,
  duration_ms     integer NULL,
  input_json      jsonb NULL,
  output_json     jsonb NULL,
  error_message   text NULL,
  UNIQUE (run_id, step_name)
);

CREATE INDEX IF NOT EXISTS idx_steps_run        ON n8n_experiments.steps (run_id);
CREATE INDEX IF NOT EXISTS idx_steps_status     ON n8n_experiments.steps (status);

-- Grants for service_role (n8n's auth role)
GRANT ALL ON ALL TABLES    IN SCHEMA n8n_experiments TO service_role;
GRANT ALL ON ALL SEQUENCES IN SCHEMA n8n_experiments TO service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA n8n_experiments GRANT ALL ON TABLES    TO service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA n8n_experiments GRANT ALL ON SEQUENCES TO service_role;

-- Verify
DO $$
BEGIN
  RAISE NOTICE 'n8n_experiments tables: %', (
    SELECT count(*) FROM information_schema.tables
    WHERE table_schema='n8n_experiments' AND table_name IN ('runs','steps')
  );
END $$;
