# Phase 01 — Vercel + Railway Deployment

**Goal:** Cut over the Compliance Agent from Contabo VPS + Cloudflare Tunnel to **Vercel (frontend-v3)** + **Railway (backend + Postgres + Inngest)** with no regression in functionality, observability, or durability.

**Owner:** kingu
**Date:** 2026-05-08
**Inputs:** `.planning/codebase/*.md` (STACK, INTEGRATIONS, ARCHITECTURE, STRUCTURE, CONVENTIONS, TESTING, CONCERNS)

## Success criteria

- [ ] `https://<frontend-domain>` (Vercel) loads the reviewer console; Supabase Auth sign-in works; calls list, HITL queue, deals, tracker, observability dashboard render.
- [ ] `https://<backend-domain>` (Railway) returns 200 from `/healthz` and `/docs`; OpenAPI loads.
- [ ] An audio upload from the Vercel frontend lands in Supabase Storage, creates a `calls` row, fires the Inngest event, runs all 6 workflow steps, and returns a verdict — end-to-end inside 7 minutes.
- [ ] `alembic upgrade head` runs cleanly on the production Postgres; `vector` extension is installed.
- [ ] Sentry (or GlitchTip) receives errors from both surfaces; a synthetic error is visible in the dashboard.
- [ ] No `localhost` or Tailscale IP remains in `allowed_origins` for the production deploy.
- [ ] Auto-deploy triggers on push to `main` (Vercel native + Railway GitHub integration).

## Pre-flight (do once, before any task below)

- [ ] Decide Postgres host: **A** keep Supabase Postgres (recommended — parity with Storage/Auth) or **B** Railway Postgres add-on (cheaper, but Storage stays Supabase). _Default: A._
- [ ] Decide error tracker: **A** Sentry SaaS (5k events/month free, fastest), **B** self-host GlitchTip on Railway (matches today), or **C** skip in MVP. _Default: A._
- [ ] Rotate the GitHub PAT noted in `claude-progress.txt` line 73; store new value as `GH_PAT` repo secret.
- [ ] Provision: Vercel project, Railway project + Postgres + backend service, Inngest cloud account, Sentry org+project (if A).

## Task list

### T1 — Repo prep (BLOCKS_DEPLOY fixes that ship before any deploy click)

- [ ] **T1.1** Stream uploads straight to Supabase Storage; drop `./uploads` writes. Files: `backend/app/routes.py:118-205`, `backend/app/storage/supabase_backend.py`. Reduce `max_file_size` from 50 MB → 25 MB (env-driven).
- [ ] **T1.2** Add Alembic migration `xxxx_enable_pgvector.py` with `op.execute("CREATE EXTENSION IF NOT EXISTS vector;")`. Run `alembic upgrade head` against staging DB to verify.
- [ ] **T1.3** Make `allowed_origins` env-only; remove the hardcoded localhost + `vmi2808153.tail1ba54c.ts.net` defaults from `backend/app/config.py:22`. Add `ENVIRONMENT=production` guard that rejects `localhost` / `127.0.0.1` entries.
- [ ] **T1.4** Add startup guard in `backend/app/main.py` lifespan: refuse to start if `dev_all_admin=True` AND `sentry_environment=='production'`.
- [ ] **T1.5** Replace `==` with `secrets.compare_digest` for `ADMIN_KEY` checks in `backend/app/routes.py:68-71`. Add basic rate limiting (e.g. `slowapi`) on admin routes.
- [ ] **T1.6** Idle-claim loop: wrap shutdown in `await asyncio.wait_for(idle_task, timeout=5)` so Railway's 15-second SIGTERM grace is honored. Files: `backend/app/main.py:51-78,116`.
- [ ] **T1.7** Pool tuning: bump `pool_size=15`, `max_overflow=30` in `backend/app/database.py`. Confirm Supabase pooler endpoint usable as `DATABASE_URL` (transaction mode), session-mode pooler as `MIGRATION_DATABASE_URL`.

### T2 — Frontend on Vercel

- [ ] **T2.1** Add `frontend-v3/vercel.json` (output mode = standalone is already in `next.config.mjs`; Vercel handles this natively).
- [ ] **T2.2** Configure Vercel project: import `frontend-v3/` as root, framework = Next.js 16, Node 22, install command `npm ci`.
- [ ] **T2.3** Add Vercel env vars (Production + Preview):
  - `NEXT_PUBLIC_API_BASE` → `https://<railway-backend>.up.railway.app`
  - `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`
  - `NEXT_PUBLIC_SENTRY_DSN`, `NEXT_PUBLIC_SENTRY_ENVIRONMENT=production`
  - `SENTRY_AUTH_TOKEN`, `SENTRY_ORG`, `SENTRY_PROJECT` (build-time uploads)
- [ ] **T2.4** Confirm `next.config.mjs` rewrites `/api/*` → backend so same-origin calls keep working through Vercel. If using direct CORS instead, document it.
- [ ] **T2.5** Hook Vercel git auto-deploy on `main` (production) and on PRs (preview).
- [ ] **T2.6** Run smoke test: open the deployed URL, sign in with Supabase Auth, navigate every top-level route. Capture results.

### T3 — Backend on Railway

- [ ] **T3.1** Verify `backend/Dockerfile` is Railway-friendly (CMD `uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8001} --workers 2`). Adjust if Railway requires `PORT` env binding.
- [ ] **T3.2** Create Railway service from GitHub repo, source dir `backend/`, Dockerfile builder.
- [ ] **T3.3** Add Railway env vars (mirror `.env.example` plus production secrets):
  - DB: `DATABASE_URL` (Supabase pooler, port 6543, transaction mode), `MIGRATION_DATABASE_URL` (Supabase pooler, port 5432, session mode)
  - Supabase: `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`
  - LLM: `OPENROUTER_API_KEY` + `ACTIVE_PROVIDER=openrouter` (or whichever)
  - STT: `DEEPGRAM_API_KEY` (+ optional secondary keys)
  - Inngest: `INNGEST_EVENT_KEY`, `INNGEST_SIGNING_KEY`, `INNGEST_ENV=production`
  - Storage: `STORAGE_BACKEND=supabase`, `SUPABASE_STORAGE_BUCKET=call-audio`
  - Observability: `SENTRY_DSN`, `SENTRY_ENVIRONMENT=production`, `SENTRY_TRACES_SAMPLE_RATE=0.1`, `PROMETHEUS_ENABLED=true`
  - CORS: `ALLOWED_ORIGINS=https://<frontend-domain>`
  - Safety: `DEV_ALL_ADMIN=false`, `ADMIN_KEY=<rotated>`
- [ ] **T3.4** Generate Railway public domain. Confirm `https://<railway-backend>/healthz` returns 200 and `/docs` loads.
- [ ] **T3.5** Run Alembic on first boot: add a Railway `release` step (custom start command) `alembic -c alembic.ini upgrade head && uvicorn app.main:app …`, OR a one-shot Railway service that runs `alembic upgrade head`.
- [ ] **T3.6** Configure Railway healthcheck: path `/healthz`, expected 200, restart policy `ON_FAILURE`, max retries 3.
- [ ] **T3.7** Hook Railway GitHub auto-deploy on `main`.

### T4 — Inngest wiring

- [ ] **T4.1** In Inngest cloud, create app `compliance-agent` (production env).
- [ ] **T4.2** Set "Sync URL" to `https://<railway-backend>/api/inngest`. Save and verify Inngest dashboard shows all 6 functions discovered.
- [ ] **T4.3** Send a manual `call/uploaded` event from the Inngest dashboard with a known `call_id`; confirm it runs end-to-end in Railway logs.
- [ ] **T4.4** Set up Inngest billing alert at 80% of free tier (50k runs/month).

### T5 — Observability cutover (Sentry SaaS path)

- [ ] **T5.1** Create Sentry org + 2 projects (`compliance-backend` Python/FastAPI, `compliance-frontend` Next.js).
- [ ] **T5.2** Add scrub rules: any field matching `*_key`, `*_secret`, `service_role_key`, `password`.
- [ ] **T5.3** Trigger a synthetic error from each surface; verify it appears in Sentry within 30s.
- [ ] **T5.4** Decide on metrics path: skip Prometheus in MVP and rely on Railway built-in metrics, OR enable Grafana Cloud and point Prometheus remote-write at it. Document the call.

### T6 — End-to-end verification

- [ ] **T6.1** Upload a real test call via the Vercel UI. Watch Railway logs and Inngest dashboard step-by-step. All 6 steps green.
- [ ] **T6.2** Open the call detail page; confirm transcript, checkpoints, score, deal rollup render.
- [ ] **T6.3** Run the HITL flow: claim a call as a reviewer, change a verdict, save, verify audit_log row.
- [ ] **T6.4** Synthetic failure test: send a malformed audio; confirm watchdog flips status to `failed` and surfaces in `/api/observability/failed-jobs`.
- [ ] **T6.5** Lighthouse + axe quick pass on the deployed Vercel site.

### T7 — Cutover hygiene

- [ ] **T7.1** Update `README.md` (replace "amina VPS" deploy section with Vercel + Railway).
- [ ] **T7.2** Move secrets from local `.env` files to Vercel/Railway dashboards; delete any committed `.env` (none should be committed already).
- [ ] **T7.3** Decide what happens to the Contabo deploy: keep as warm standby (recommended for first 30 days) or decommission. Document.
- [ ] **T7.4** Add `DEPLOYMENT.md` with the exact provider screens to click + env-var matrix.

### T8 — Stretch / post-launch (don't block phase close)

- [ ] **T8.1** Snapshot feature flags into `calls.feature_flags` JSON at pipeline start (CONCERNS#feature-flags).
- [ ] **T8.2** Add `secrets.compare_digest`-based rate-limited admin auth replaced fully by Supabase RLS roles.
- [ ] **T8.3** 90-day Storage retention job + `/api/admin/storage-usage` endpoint.
- [ ] **T8.4** Inngest self-host fallback runbook for the day volume crosses the free-tier ceiling.

## Risk register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------:|-------:|------------|
| Vercel/Railway proxies buffer 25 MB upload | M | M | T1.1 streams; if still too big, push directly to Supabase from the browser via a signed URL |
| Inngest sync fails on Railway (signing key wrong) | M | H | T4.2 verifies discovery before T6.1 |
| Supabase pooler runs out of connections under load | L | H | Use transaction-mode pooler (port 6543) for runtime, session-mode (5432) only for Alembic |
| Sentry trace sample 0.1 too noisy in prod | L | L | Adjustable env var, no redeploy needed |
| pgvector migration runs after first request, RAG breaks | M | M | T3.5 forces alembic upgrade as part of release step |
| `dev_all_admin` accidentally true | L | Critical | T1.4 startup guard |

## Out of scope for this phase

- Self-hosted GlitchTip + Grafana + Loki on Railway (deferred to Wave-2 if SaaS becomes a problem)
- Moving Supabase → Railway Postgres (only if cost or compliance requires)
- Switching object storage from Supabase to S3/R2 (`storage_backend=s3` already supported; flip when needed)
- Publishing OpenAPI spec to a hosted docs site (Vercel `/docs` proxy is fine)

## Definition of Done

A reviewer can sign in at the Vercel URL, upload a call, watch all 6 pipeline steps complete in the Inngest dashboard, mark a verdict in HITL, see it appear in audit_log, and observe the corresponding Sentry breadcrumbs — all on `main` of the GitHub repo, with auto-deploy on push verified.
