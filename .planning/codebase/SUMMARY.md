# Codebase Map — Summary

**Generated:** 2026-05-08
**Project:** Compliance Agent v1 (call analysis + HITL review console)
**Maps:** 7 documents, 4 parallel mapper agents (gsd-codebase-mapper, model=haiku)

## What this codebase is

A multi-engine compliance call analysis system that:

1. Receives audio uploads through a Next.js console.
2. Persists each call to Supabase Postgres + Supabase Storage and emits an Inngest event.
3. Runs a 6-step durable Inngest workflow (`process_call`): download → transcribe (consensus across Deepgram/AssemblyAI/Speechmatics/Groq/OpenAI/Cohere/Gemini) → detect metadata → analyze checkpoints (tiered Smart Agent: Gemini Flash → Claude Sonnet 4.6 escalation, with optional pgvector pre-filter) → score → finalize.
4. Surfaces verdicts to reviewers via a HITL console with claim locks, rejection factory, and per-deal rollups.
5. Exposes observability via Sentry/GlitchTip + Prometheus metrics + Grafana + Loki + Inngest dashboard.
6. Owns its schema via ~60 Alembic migrations on Postgres 16 with the `vector` extension.

## Map documents (in `.planning/codebase/`)

| Doc | Lines | Focus |
|-----|------:|-------|
| STACK.md | 223 | Languages, runtimes, frameworks, key deps with versions, build tooling, env config |
| INTEGRATIONS.md | 268 | External APIs, DBs, auth, observability, workflow engine, plus Railway/Vercel migration table |
| ARCHITECTURE.md | 277 | 8-layer pattern, 6-step Inngest pipeline, data flow, abstractions, entry points |
| STRUCTURE.md | 497 | Full directory tree (~150 files annotated), naming conventions, where to find each feature |
| CONVENTIONS.md | 527 | Backend Python style, Frontend TS/React 19/Next 16 patterns, shared conventions |
| TESTING.md | 764 | pytest layout, fixtures, mocking; vitest unit + Playwright e2e + axe accessibility |
| CONCERNS.md | 294 | Tech debt, bugs, security, performance, fragility — tagged for deploy impact |

## Tech stack snapshot

- **Backend:** FastAPI 0.115 (Python 3.12), SQLAlchemy 2.0 + Alembic 1.14, asyncpg + psycopg2, Pydantic v2, Inngest 0.5+, sentry-sdk[fastapi], prometheus-fastapi-instrumentator, pgvector ≥0.3, supabase ≥2.5, boto3 1.34, sse-starlette.
- **Frontend:** Next.js 16.2 + React 19.2 (App Router), TanStack Query 5, TanStack Table 8, React Hook Form + Zod, Zustand, shadcn 4.6 + Radix + Tailwind 4, @sentry/nextjs 10.51, @supabase/supabase-js 2.105.
- **Tests:** pytest + pytest-asyncio + pytest-cov; vitest 4 + Playwright 1.59 + axe-core.
- **Infra (current):** Contabo VPS + Cloudflare Tunnel, Docker Compose, GlitchTip + Prometheus + Grafana + Loki self-hosted.

## Deployment-relevant findings (CONCERNS)

**[BLOCKS_DEPLOY] — must fix before Railway/Vercel goes live:**

1. File uploads use local `./uploads` then sync to Supabase. Railway is ephemeral; must stream directly to Supabase Storage. Drop max body size from 50 MB to 25 MB to avoid Vercel/Railway proxy limits.
2. Inngest webhook endpoint `/api/inngest` must be publicly reachable so `call/uploaded` events fire — needs Railway's public domain (or a CF tunnel) set in Inngest config.
3. pgvector extension must be auto-provisioned on the Postgres target (Alembic migration `CREATE EXTENSION IF NOT EXISTS vector;`).

**[DEPLOY_ATTENTION] — production correctness/cost:**

- CORS `allowed_origins` hardcodes localhost + a Tailscale IP — must be replaced by the production domains.
- GlitchTip + Prometheus + Grafana + Loki are self-hosted; for MVP either skip them or route to Sentry SaaS / Grafana Cloud / Better Stack.
- Idle-claim sweeper and `pg_dump_nightly`/`redispatch_watchdog` crons need to survive Railway restarts cleanly. Idle sweeper should `wait_for(timeout=5)` on shutdown.
- Connection pool (10 + 20 overflow) is borderline for Railway; bump to 15–20 + 30 overflow or use Supabase PgBouncer (transaction mode).

**[SECURITY] — pre-deploy checklist:**

- `DEV_ALL_ADMIN=true` must be hard-blocked when `SENTRY_ENVIRONMENT=production`.
- `secrets.compare_digest` for `ADMIN_KEY` plus rate limiting.
- Sentry scrubs already on (`send_default_pii=False`); add explicit `*_key`/`*_secret` scrub rules.

**[SCALING] — to monitor post-launch:**

- Inngest free tier ≤50k runs/month: at ~30 events per call, plan a self-hosted fallback (Apache 2.0 binary) once volume grows.
- Supabase Storage has no retention — set 90-day lifecycle and a `DELETE FROM calls` background job.

## Architectural posture for Vercel + Railway

| Layer | Today (Contabo) | Target | Notes |
|-------|-----------------|--------|-------|
| Frontend | Next.js standalone behind CF Tunnel | **Vercel** (native) | Already production-ready; Sentry already wired |
| Backend API | FastAPI on Docker behind CF Tunnel | **Railway** (Dockerfile already exists, Python 3.12 base) | Need public domain for Inngest, increase pool |
| Postgres | Supabase (managed) | **Supabase or Railway Postgres** (with pgvector) | Recommend keep Supabase to retain Auth/Storage parity |
| Auth | Supabase Auth (JWKS) | Supabase Auth | No change |
| Object storage | Supabase Storage / S3 | Supabase Storage | Stop using local `./uploads`; stream to Storage |
| Workflow engine | Inngest cloud (free tier) | **Inngest cloud** | Configure env, expose `/api/inngest` publicly |
| Observability | GlitchTip + Prom + Grafana + Loki | **Sentry SaaS** (or skip in MVP) + Railway logs + Better Stack | Drop the docker-compose observability stack for v1 deploy |
| CDN/edge | Cloudflare Tunnel | Vercel edge + Railway public URL | Replace tunnel; configure CORS |

## Cross-cutting risks for the migration

1. **CORS / origin coupling**: production frontend domain must be added to `allowed_origins`; Vercel preview URLs are dynamic so consider a regex/allowlist by env.
2. **Streaming endpoints (sse-starlette)**: Railway supports long-lived HTTP fine; Vercel serverless functions do not — but the Next.js app on Vercel only proxies, so this is OK.
3. **File-upload size**: 50 MB current limit → reduce to 25 MB and stream-pass through to Supabase to dodge Vercel/Railway proxy buffers.
4. **Background loops**: any task that holds a DB connection forever (idle sweeper) will fight the pool — convert to Inngest cron or shorten loop intervals.
5. **Secrets matrix**: ~25 env vars across 4 LLM providers + 4 STT providers + Supabase + S3 + Sentry + Inngest. Needs a single canonical .env.example update + Railway/Vercel parity check.

## Next step

Hand off to `/gsd:add-phase` for the **Vercel + Railway deployment phase**. The plan is drafted in `.planning/phases/01-vercel-railway-deploy/PLAN.md`.
