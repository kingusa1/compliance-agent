# External Integrations

**Analysis Date:** 2026-05-08

## APIs & External Services

**Large Language Models (LLM):**
- OpenRouter (primary, default)
  - SDK: httpx (raw HTTP POST to `https://openrouter.ai/api/v1/chat/completions`)
  - Auth: `OPENROUTER_API_KEY`
  - Model: `OPENROUTER_MODEL` (default: `anthropic/claude-sonnet-4-6`)
  - Implementation: `backend/app/analysis.py` → `_call_openrouter()`
  - Used for: Compliance analysis, agent reasoning

- Anthropic (Claude)
  - SDK: httpx (raw HTTP POST to `https://api.anthropic.com/v1/messages`)
  - Auth: `ANTHROPIC_API_KEY`
  - Model: `ANTHROPIC_MODEL` (default: `claude-sonnet-4-6`)
  - Implementation: `backend/app/analysis.py` → `_call_anthropic()`
  - Used for: Escalation decisions, complex reasoning

- Google Gemini
  - SDK: httpx (raw HTTP POST, implied API endpoint)
  - Auth: `GEMINI_API_KEY`
  - Model: `GEMINI_MODEL` (default: `gemini-2.0-flash`)
  - Implementation: `backend/app/analysis.py` → `_call_gemini()`
  - Used for: First-pass agent analysis (Feature flag: `USE_AGENT_ANALYZER`, default: false)

- OpenAI (GPT-4)
  - SDK: httpx (raw HTTP POST to `https://api.openai.com/v1/chat/completions`)
  - Auth: `OPENAI_API_KEY`
  - Model: `OPENAI_MODEL` (default: `gpt-4o`)
  - Implementation: `backend/app/analysis.py` → `_call_openai()`
  - Used for: Alternative provider fallback

**Speech-to-Text (STT):**
- Deepgram (primary)
  - SDK: deepgram-sdk 3.7.0
  - Auth: `DEEPGRAM_API_KEY`
  - Model: nova-3 (diarization + punctuation + smart formatting)
  - Implementation: `backend/app/transcription.py` → `transcribe_audio()`
  - Optional: Empty key disables Deepgram; tests mock it
  - Used for: Audio transcription with speaker diarization

- AssemblyAI (secondary)
  - SDK: httpx (raw HTTP POST, implied)
  - Auth: `ASSEMBLYAI_API_KEY`
  - Implementation: `backend/app/assemblyai_transcription.py`
  - Parallel mode: Called via asyncio.gather in pipeline

- Speechmatics (secondary)
  - SDK: (implied, SDK or raw HTTP)
  - Auth: `SPEECHMATICS_API_KEY`
  - Parallel mode: Called via asyncio.gather in pipeline

- Groq (secondary)
  - Implementation: `backend/app/groq_transcription.py`
  - Parallel mode: Called via asyncio.gather in pipeline

- Cohere (secondary)
  - Implementation: `backend/app/cohere_transcription.py`
  - Parallel mode: Called via asyncio.gather in pipeline

## Data Storage

**Primary Database:**
- Postgres 16
  - Connection: `DATABASE_URL` (SQLAlchemy DSN format: `postgresql+psycopg2://...`)
  - Migration connection: `MIGRATION_DATABASE_URL` (session-mode pooler for Alembic, port 5432)
  - Client: SQLAlchemy 2.0.35 ORM + psycopg2-binary 2.9.10 (sync) + asyncpg 0.30.0 (async)
  - Pooling: pool_size=10, max_overflow=20, pool_recycle=1800s
  - Connection timeout: 10s, keepalives enabled
  - Hosted option: Supabase (managed Postgres)
  - Migrations: Alembic in `backend/alembic/versions/`
  - Schema: Tables for calls, checkpoints, profiles, organizations, deals, audit logs, etc.

**Vector Search:**
- pgvector extension (on Postgres)
  - Package: pgvector 0.3+
  - Used for: Semantic search on agent learnings
  - Table: `agent_learnings` with embedding column
  - Implementation: `backend/app/rag/embed.py`

**Object Storage:**
- Supabase Storage (default, managed)
  - SDK: supabase 2.5+
  - Auth: `SUPABASE_SERVICE_ROLE_KEY`
  - Bucket: `SUPABASE_STORAGE_BUCKET` (default: "call-audio")
  - Backend class: `SupabaseBackend` in `backend/app/storage/supabase_backend.py`
  - Used for: Call audio files, document uploads

- S3-compatible (AWS S3, MinIO, Cloudflare R2)
  - SDK: boto3 1.34.144
  - Auth: `S3_ACCESS_KEY`, `S3_SECRET_KEY`
  - Endpoint: `S3_ENDPOINT` (optional; empty = AWS default)
  - Region: `S3_REGION` (default: us-east-1)
  - Buckets: `S3_BUCKET` (default: "call-audio"), `BACKUP_BUCKET` (default: "backups")
  - Backend class: `S3Backend` in `backend/app/storage/s3_backend.py`
  - Feature: Works locally with MinIO during dev, AWS in prod (same code)
  - Signed URLs: boto3 pre-signer, 1 hour default expiry

**Storage Selection:**
- Configuration: `STORAGE_BACKEND` (default: "supabase")
- Runtime factory: `backend/app/storage/__init__.py` instantiates correct backend

**Session Storage (Optional):**
- Redis (for GlitchTip and potential future use)
  - In `docker-compose.observability.yml`
  - Used by: GlitchTip Celery task queue

## Authentication & Identity

**Auth Provider:**
- Supabase Auth (managed)
  - Provider: Supabase (Postgres-backed)
  - JWT verification: JWKS asymmetric (ECC P-256 via PyJWKClient)
  - JWKS endpoint: `{SUPABASE_URL}/auth/v1/.well-known/jwks.json`
  - Configuration:
    - `SUPABASE_URL`
    - `SUPABASE_ANON_KEY` (public key for frontend)
    - `SUPABASE_JWT_SECRET` (symmetric fallback, not currently used; JWKS preferred)
  - Implementation: `backend/app/auth.py`
    - `verify_jwt()`: Token verification
    - `current_user()`: User profile lookup
    - `require_lead()`: Role-based access control
  - Frontend client: `@supabase/supabase-js` 2.105.1

**Authorization Model:**
- Role-based: "admin", "lead", "reviewer" (from Profile.role)
- Dev bypass: `DEV_ALL_ADMIN=true` treats all users as admin (dev only)
- Admin key: Optional `ADMIN_KEY` for override/testing

## Monitoring & Observability

**Error Tracking:**
- GlitchTip (self-hosted, Sentry-compatible)
  - Backend SDK: sentry-sdk[fastapi] 2.18.0
  - Frontend SDK: @sentry/nextjs 10.51.0
  - Configuration:
    - `SENTRY_DSN` (backend, empty = no-op)
    - `SENTRY_ENVIRONMENT` (default: development)
    - `SENTRY_TRACES_SAMPLE_RATE` (default: 0.1)
    - `NEXT_PUBLIC_SENTRY_DSN` (frontend)
    - `NEXT_PUBLIC_SENTRY_ENVIRONMENT` (frontend)
  - Self-hosting: `docker-compose.observability.yml`
    - Service: glitchtip-web (port 8080, tunneled)
    - Requires: glitchtip-postgres (Postgres 16), glitchtip-redis (Redis 7)
    - Environment: GlitchTip v4.1, Celery workers included
  - **Deployment note:** GlitchTip requires self-hosting (not a managed service); use Railway container or alternative cloud provider

**Metrics (Prometheus):**
- Prometheus 2.x (self-hosted)
  - Collection method: `prometheus-fastapi-instrumentator` 7.0.2 (auto-instruments FastAPI)
  - Custom metrics: `backend/app/observability_metrics.py`
    - `pipeline_step_duration_seconds` (histogram, by step)
    - `llm_calls_total` (counter, by model + escalated)
    - `llm_call_duration_seconds` (histogram, by model)
  - Scrape target: `compliance-backend:8001/metrics`
  - Self-hosting: `docker-compose.observability.yml`
    - Service: prometheus (port 9090, internal only)
    - Data volume: prom-data
  - **Deployment note:** Prometheus is self-hosted; Railway/Vercel doesn't provide built-in Prometheus. Consider external Prometheus-as-a-service (e.g., Grafana Cloud, Datadog) or self-host on separate container

**Logs:**
- Structured logging: python-json-logger 2.0.7 (JSON output)
- Transport: `sentry-sdk` captures errors
- Log aggregation (optional): Loki (self-hosted)
  - In `docker-compose.observability.yml`
  - Service: loki (port 3100, internal)
  - Log shipper: Promtail (configured to read Docker container logs)

**Visualization:**
- Grafana (self-hosted dashboard)
  - Service: grafana (port 3001)
  - Admin password: `GRAFANA_ADMIN_PASSWORD`
  - Dashboards: (custom, not listed in manifests)
  - Data sources: Prometheus (localhost:9090), Loki (localhost:3100)
  - **Deployment note:** Grafana is self-hosted; must be deployed separately or via container

**Observability Decision:**
- **Local dev:** All observability services run in `docker-compose.observability.yml`
- **Production (Railway/Vercel):**
  - GlitchTip: Must be self-hosted on Railway or external provider (Sentry SaaS alternative: sentry.io)
  - Prometheus: Must be self-hosted on Railway or use external service (Grafana Cloud, Datadog)
  - Grafana: Must be self-hosted or use Grafana Cloud
  - Recommendation: Migrate to managed observability (Grafana Cloud, Datadog, or Sentry SaaS) for production

## Workflow Engine

**Inngest (Durable Workflow Orchestration):**
- SDK: inngest 0.5-1.0
- Purpose: Async job queue, retry logic, durability for long-running tasks
- Client: `backend/app/inngest_client.py`
  - App ID: "compliance-agent"
  - Production flag: `INNGEST_ENV` (default: dev)
- Functions (workflows):
  - `process_call`: Main compliance pipeline (Phase D.1)
  - `process_call_reanalyze`: Re-run analysis
  - `rag_ingest_call_fn`: Ingest call data for RAG
  - `rag_ingest_script_fn`: Ingest script data for RAG
  - `redispatch_watchdog`: Monitor and redispatch failed jobs
  - `pg_dump_nightly`: Backup Postgres
- Feature flag: `USE_INNGEST_PIPELINE` (default: false; when true, upload handler emits `call/uploaded` event)
- Exposed endpoint: `/api/inngest` (FastAPI integration via `inngest.fast_api.serve()`)
- **Deployment note:** Inngest is a managed service (https://inngest.com); sign-up required. API key/env setup needed for Railway backend.

## Email & Communication

**Email Routes:**
- Module: `backend/app/email_routes.py`
- Provider: Not specified in config (may be SMTP, SendGrid, or stub)
- Implementation: (details not exposed in config.py)

## Webhooks & Callbacks

**Incoming Webhooks:**
- None detected in public API surface

**Outgoing Webhooks:**
- None detected in config

**Realtime:**
- Supabase Realtime (optional, via @supabase/supabase-js on frontend)
  - Auto-notifies UI of database changes
  - Not explicitly configured in backend

## Environment Configuration Summary

**Required Environment Variables for Deployment:**
- `SUPABASE_URL` - Supabase project URL
- `SUPABASE_SERVICE_ROLE_KEY` - Supabase service key (server-side only)
- `DATABASE_URL` - Postgres connection string
- `MIGRATION_DATABASE_URL` - Alembic pooler connection (port 5432)
- At least one of: `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `OPENAI_API_KEY`
- `ACTIVE_PROVIDER` - Which LLM to use (default: openrouter)

**Optional but Important:**
- `DEEPGRAM_API_KEY` - Deepgram STT (empty = disabled)
- `SPEECHMATICS_API_KEY`, `ASSEMBLYAI_API_KEY` - Additional STT
- `SENTRY_DSN` - Error tracking (GlitchTip endpoint)
- `STORAGE_BACKEND` - "supabase" or "s3" (default: supabase)
- `S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` - For S3 backend
- `INNGEST_ENV` - dev or production (for Inngest SaaS)

**Secrets Location:**
- All stored in `.env` file (development only; **NEVER commit to git**)
- Production: Environment variables via Railway/Vercel dashboard or secret management tool

## Integration Architecture for Railway/Vercel Deployment

| Service | Type | Hosted | Managed | Action for Production |
|---------|------|--------|---------|----------------------|
| Postgres | Database | Supabase | Yes | Use Supabase-hosted DB |
| Supabase Auth | Auth | Supabase | Yes | Keep as-is |
| Deepgram | STT | Deepgram | Yes | Keep as-is (API key) |
| OpenRouter/Anthropic | LLM | External | Yes | Keep as-is (API keys) |
| Supabase Storage | File Storage | Supabase | Yes | Keep as-is (default) |
| S3/MinIO | File Storage | Optional | No | Migrate to AWS S3 or Cloudflare R2 |
| GlitchTip | Error Tracking | Self-hosted | No | **Migrate to Sentry SaaS or self-host on Railway** |
| Prometheus | Metrics | Self-hosted | No | **Self-host on Railway or use Grafana Cloud** |
| Grafana | Visualization | Self-hosted | No | **Self-host on Railway or use Grafana Cloud** |
| Loki | Logs | Self-hosted | No | **Use Railway's built-in logs or self-host** |
| Inngest | Workflow Engine | SaaS | Yes | **Sign up at inngest.com, configure API key** |
| Redis | Session/Queue | Self-hosted | No | **Optional; self-host on Railway if using GlitchTip** |

---

*Integration audit: 2026-05-08*
