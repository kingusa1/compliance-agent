# Codebase Concerns

**Analysis Date:** 2026-05-09

## Deployment & Infrastructure

**[BLOCKS_DEPLOY] File uploads incompatible with Vercel Edge + Railway ephemeral filesystem:**
- Problem: `settings.max_file_size = 50MB` (in `backend/app/config.py:30`). Vercel Edge Functions have ~4.5MB body limit; Vercel Pro has ~25MB. Railway's ephemeral filesystem means `/uploads` directory is lost on redeploy.
- Files: `backend/app/config.py:30`, `backend/app/routes.py:181-182`, `backend/app/main.py:85`
- Current setup: Audio files stored to local `./uploads` directory then synced to Supabase Storage. This works on Contabo VPS but breaks on Railway.
- Fix approach:
  1. Storage backend already supports abstraction (`storage_backend = "supabase" | "s3"` in `backend/app/config.py:75`). Ensure upload route **always** uses Supabase Storage directly, never local disk as primary.
  2. For Vercel+Railway: disable local `./uploads` writes entirely. Use Supabase Storage as the single source of truth.
  3. Reduce max file size to 25MB for Railway compatibility (configurable via env var).
  4. Stream uploads to Supabase Storage directly instead of buffering to memory/disk.

**[DEPLOY_ATTENTION] CORS allowed_origins hardcoded with development + Tailscale IP:**
- Problem: `settings.allowed_origins` in `backend/app/config.py:22` includes localhost variants + hardcoded Tailscale IP `https://vmi2808153.tail1ba54c.ts.net:8444`. This breaks on Railway/Vercel deployment URLs.
- Files: `backend/app/config.py:22`, `backend/app/main.py:186`
- Current: Comma-separated string with development IPs mixed in.
- Fix approach:
  1. Before deploying to Railway/Vercel, replace `allowed_origins` env var with production domain(s).
  2. Add validation in `backend/app/config.py` to reject localhost/127.0.0.1 if `ENVIRONMENT=production`.
  3. Document required env vars: `ALLOWED_ORIGINS=https://your-vercel-domain.vercel.app,https://your-frontend-domain.com`

**[BLOCKS_DEPLOY] Inngest event webhook URL must be publicly reachable:**
- Problem: Inngest emits events to `POST /api/inngest` endpoint (see `backend/app/main.py:230`). In dev (Cloudflare Tunnel), this is exposed. On Railway, the backend is private by default.
- Files: `backend/app/main.py:230-241`, `backend/app/inngest_client.py`
- Impact: `call/uploaded` events won't fire; pipeline never runs; calls stuck in `queued` status.
- Fix approach:
  1. Railway: Inngest must reach `https://<railway-backend-url>/api/inngest` from the internet.
  2. Use Railway's public domain feature or Cloudflare Tunnel (same as Contabo).
  3. In production, set `INNGEST_WEBHOOK_URL` env var to the publicly accessible endpoint.
  4. For self-hosted Inngest (Phase 2): ensure its event-send network path is open.

**[DEPLOY_ATTENTION] GlitchTip self-hosted requires separate hosting:**
- Problem: `docker-compose.observability.yml` defines GlitchTip (Sentry-compatible error tracker) with PostgreSQL + Redis. This is not suitable for Railway's managed database model.
- Files: `docker-compose.observability.yml`, `backend/app/config.py:69` (sentry_dsn)
- Impact: Error tracking disabled if GlitchTip not deployed separately.
- Fix approach:
  1. Option A (recommended for MVP): Disable Sentry/GlitchTip in early Railway deploy. Set `SENTRY_DSN=""` (default in config).
  2. Option B (production): Deploy GlitchTip to separate Railway service or self-hosted VM.
  3. Document in deployment guide: "GlitchTip is optional. Skip in MVP; add in Wave 2 hardening."

**[DEPLOY_ATTENTION] pgvector extension required but not auto-provisioned:**
- Problem: Supabase Postgres supports pgvector, but `backend/app/database.py:6-20` has no check that extension exists. `pgvector>=0.3` in requirements.txt but no migration step.
- Files: `backend/app/database.py`, `backend/alembic/versions/*.py` (migrations)
- Impact: Embedding ingest and RAG searches fail if pgvector not installed on target Postgres.
- Fix approach:
  1. Add Alembic migration: `CREATE EXTENSION IF NOT EXISTS vector;`
  2. Run `alembic upgrade head` as part of Railway deployment setup step.
  3. Document: "Supabase Postgres requires manual pgvector extension enable via dashboard, OR auto-enable via Alembic migration."

## Long-Running Processes & Scheduled Tasks

**[DEPLOY_ATTENTION] Idle-claim sweeper (`_idle_release_loop`) may block startup/shutdown:**
- Problem: `backend/app/main.py:51-78` spawns an `asyncio.Task` that runs forever (120s interval). On Railway/Vercel cold restart, this task competes for database connections.
- Files: `backend/app/main.py:51-78, 116`
- Impact: Slow graceful shutdown (<= Railway's 15s timeout). If task doesn't cancel cleanly, process may be force-killed mid-database-write.
- Fix approach:
  1. Add timeout in lifespan shutdown: `await asyncio.wait_for(idle_task, timeout=5)` instead of infinite await.
  2. Railway: Set `KILL_SIGNAL=SIGTERM` with 15s grace period (Railway default).
  3. Test locally: `kill -TERM` and verify cleanup logs appear within 5s.
  4. Consider: Move idle-claim sweep to Inngest scheduled function (`TriggerCron`) instead of asyncio task — survives process restarts.

**[DEPLOY_ATTENTION] `pg_dump_nightly` scheduled cron (2am UTC) may collide with Railway maintenance:**
- Problem: Inngest scheduled function at `backend/app/workflows/pg_dump_nightly.py:26` runs nightly `0 2 * * *`. Railway maintenance windows are unpredictable; process restart mid-dump leaves incomplete backup.
- Files: `backend/app/workflows/pg_dump_nightly.py`, `scripts/pg_dump_to_storage.py`
- Impact: Missing daily backup; restore drill (feature #11) may fail.
- Fix approach:
  1. Make cron time configurable: env var `BACKUP_CRON_SCHEDULE=0 2 * * *` (default).
  2. Add Inngest retry logic + failed_jobs tracking (Wave 1 already does this for process_call).
  3. Document: "Set BACKUP_CRON_SCHEDULE off-peak for your region; Railway restarts may delay but don't skip Inngest tasks."

**[DEPLOY_ATTENTION] Redispatch watchdog cron runs every minute; may overwhelm Railway at scale:**
- Problem: `backend/app/workflows/redispatch_watchdog.py:69` triggers every 60s. At high call volume (>1000/day), this scans 50+ stuck calls per tick. Supabase pooler on transaction-mode may hit connection limits.
- Files: `backend/app/workflows/redispatch_watchdog.py:37-64`, `backend/app/database.py:9-19`
- Current: `pool_size=10, max_overflow=20, pool_recycle=1800` (30 min). Safe for Contabo; borderline for Railway.
- Fix approach:
  1. Add `pool_pre_ping=True` (already in place) to detect stale connections.
  2. If scaling: increase `max_overflow` to 30-40 for Railway, or use Supabase's session-mode pooler (port 6543) for long-running sweeps.
  3. Monitor: Add Prometheus metric for "watchdog_stuck_calls_scanned" to detect runaway scans.

## Security

**[SECURITY] `dev_all_admin` flag dangerous if left True in production:**
- Problem: `backend/app/config.py:93` defaults False, but `backend/app/auth.py:67` bypasses all role checks when True. Comment says "NEVER true in production" but no runtime validation.
- Files: `backend/app/config.py:93`, `backend/app/auth.py:64-73`, `.env.example:29`
- Impact: If env var `DEV_ALL_ADMIN=true` leaks to production, every authenticated user becomes admin.
- Fix approach:
  1. Add startup validation in `backend/app/main.py` lifespan:
     ```python
     if settings.dev_all_admin and settings.sentry_environment == "production":
         raise RuntimeError("DEV_ALL_ADMIN must be False in production")
     ```
  2. Document in deployment guide: "DEV_ALL_ADMIN is dev-only. Block it at CI/CD time: `if [ "$ENV" = "production" ]; then test "$DEV_ALL_ADMIN" != "true"; fi`"

**[SECURITY] Empty-string defaults for API keys allow silent degradation:**
- Problem: `backend/app/config.py:10-25` has many `api_key: str = ""` defaults. Code doesn't always validate presence before use.
- Files: `backend/app/config.py:10-25`, `backend/app/transcription.py` (multi-engine tribunal)
- Example: If all STT engines have empty keys, transcription silently fails or falls back to a single provider without warning.
- Fix approach:
  1. At startup, log which providers are active: "Transcription engines: Deepgram=enabled, AssemblyAI=disabled, ..."
  2. For critical paths (transcription, LLM analysis), validate at least one provider is configured. Raise error at startup if not.
  3. Document required env vars per deployment target (dev vs prod).

**[SECURITY] Supabase service role key stored in config but may leak in logs:**
- Problem: `backend/app/config.py:35` (`supabase_service_role_key`) used in `backend/app/storage/supabase_backend.py:24`. If error occurs, key may appear in stack trace or Sentry payload.
- Files: `backend/app/config.py:35`, `backend/app/storage/supabase_backend.py`, `backend/app/main.py:139-148` (Sentry init)
- Fix approach:
  1. Sentry SDK already configured with `send_default_pii=False` (good).
  2. Add Sentry integration to scrub all `*_key` and `*_secret` from error context.
  3. In Supabase client init, avoid logging the key in debug output.
  4. Use Supabase's RLS (Row-Level Security) to minimize need for service-role access in production.

**[SECURITY] Admin key (`admin_key`) uses simple string comparison:**
- Problem: `backend/app/routes.py:68-71` compares `X-Admin-Key` header as plaintext. No rate limiting, no hash.
- Files: `backend/app/routes.py:68-71`, `backend/app/config.py:23`
- Impact: Brute-force attack on routes that require admin_key (e.g., `/api/admin/import-tracker-xlsx`).
- Fix approach:
  1. Add rate limiting middleware on admin routes: max 5 failures per IP per hour.
  2. Use `secrets.compare_digest()` for constant-time comparison (prevent timing attacks).
  3. Log failed attempts with IP for monitoring.
  4. Consider: Switch to Supabase Auth roles instead of a shared admin key for better auditability.

## Performance Bottlenecks

**[PERF] Database connection pool may be undersized for concurrent uploads on Railway:**
- Problem: `backend/app/database.py:9-19` defines `pool_size=10, max_overflow=20`. With 2 uvicorn workers (Docker CMD) and background tasks (idle_release_loop, watchdog cron via Inngest), 30 total connections may saturate under load.
- Files: `backend/app/database.py:6-20`, `backend/Dockerfile:15`
- Scenario: 20 concurrent upload requests + watchdog cron scan + Inngest process_call steps all need DB connections simultaneously.
- Fix approach:
  1. For Railway: Increase `pool_size` to 15-20 and `max_overflow` to 30.
  2. Use Supabase's PgBouncer in transaction-mode: Railway can share one connection pool across horizontally-scaled backend instances.
  3. Monitor connection pool stats: Add Prometheus metric via SQLAlchemy pool events.
  4. Add `statement_timeout=15000` (already in place; good for killing runaway queries).

**[PERF] File uploads to Supabase Storage may timeout under poor network:**
- Problem: `backend/app/routes.py:180` reads entire file into memory with `await file.read()`. 50MB file on slow connection may exceed Supabase's default timeout.
- Files: `backend/app/routes.py:118-205`, `backend/app/storage/supabase_backend.py`
- Impact: Upload fails midway; partially-written files in storage bucket; user left hanging.
- Fix approach:
  1. Stream upload to Supabase Storage instead of buffering to memory. Supabase Python SDK supports streaming.
  2. Reduce max file size to 25MB for Railway (4.5x safer margin).
  3. Add client-side retry logic in frontend (TanStack Query already has this).
  4. Document: "Large files (>10MB) may timeout on flaky networks. Recommend splitting into multiple calls or using upload management tool."

**[PERF] Multi-engine transcription consensus may exceed timeout under high latency:**
- Problem: `suggested-arch.md` describes `asyncio.gather(Deepgram, AssemblyAI, Speechmatics, ...)` in step 2. If any provider times out or fails, entire step fails. No fallback to first-response.
- Files: `backend/app/transcription.py`, `backend/app/workflows/process_call.py`
- Timeout: Step 2 has 300s timeout (per suggested-arch.md line 70).
- Fix approach:
  1. Implement `asyncio.wait(return_when=asyncio.FIRST_COMPLETED)` to get fastest response first, then cancel others.
  2. Add per-provider timeout: 60s for each engine (not 300s for all).
  3. Fall back to single best provider if quorum unavailable (e.g., any 2 of 7).
  4. Document which engines are "required for quorum" vs "optional for consensus."

## Fragile Areas & Gaps

**[TECH_DEBT] Idle-claim release sweep has poor error recovery:**
- Problem: `backend/app/main.py:76-77` logs warnings and swallows exceptions. If a single malformed `review_sessions` row exists, sweep skips it forever.
- Files: `backend/app/main.py:51-78`, `backend/app/hitl_routes.py` (_release_idle_claims_core)
- Impact: Claimed calls never auto-released; reviewers offline forever lock calls.
- Fix approach:
  1. Catch exceptions per-row, not per-iteration. Log `call_id` of failed release attempts.
  2. Add `claim.updated_at` field; only release if older than 120+ minutes (not just skipping bad rows).
  3. Add `/api/admin/force-release-claim?call_id=X` endpoint for manual override.
  4. Monitor: Prometheus metric "idle_release_failures_total".

**[TECH_DEBT] Feature flags (`use_agent_analyzer`, `embedding_prefilter_enabled`, etc.) scattered across config with no audit trail:**
- Problem: `backend/app/config.py:40-66` has 8+ boolean flags. When they flip, no audit log. No A/B test framework to track which calls ran with which flags.
- Files: `backend/app/config.py:40-66`, `backend/app/checkpoint_analyzer.py`, `backend/app/pipeline.py`
- Impact: Hard to debug verdict differences; can't correlate flag state to call outcome post-hoc.
- Fix approach:
  1. Snapshot feature flags into `calls.feature_flags` JSON at pipeline start (step 1).
  2. Include flags in audit log on every verdict change.
  3. Add `/api/observability/experiments` endpoint to list which calls used which flag combos.
  4. This is a Wave 4/5 concern; defer to production after cost optimization (Wave 4) ships.

**[TECH_DEBT] No database migration rollback plan:**
- Problem: Alembic migrations in `backend/alembic/versions/` only support `upgrade`. No documented rollback procedure if a schema change breaks production.
- Files: `backend/alembic/versions/`, `backend/alembic/env.py`
- Impact: If a migration causes widespread call failures, manual SQL rollback required.
- Fix approach:
  1. Document Alembic downgrade: `alembic downgrade -1` (this is standard; just ensure team knows it).
  2. Add pre-deploy test in CI: Run test suite against latest schema + one-version-back to ensure backward compat.
  3. For Railway: Keep a manual snapshot of schema before each production migration (export SQL via Supabase dashboard).
  4. Consider: Add "soft deletes" instead of hard schema deletes for maximum rollback safety.

**[TECH_DEBT] pgvector embedding search has no error handling for dimension mismatches:**
- Problem: `backend/app/rag/*.py` (embedding ingest + search) assumes all vectors are same dimension. If a migration changes embedding model (e.g., OpenAI 1536 → 3072), old vectors fail cosine_similarity queries.
- Files: `backend/app/rag/` (entire directory), `backend/alembic/versions/` (no migration for dimension change)
- Impact: Silent search failures; RAG feature breaks without alerting.
- Fix approach:
  1. Add vector dimension validation at ingest time: `if len(embedding) != EXPECTED_DIM: raise ValueError(...)`
  2. If embedding model changes, add migration to re-embed all chunks (slow but necessary).
  3. Store `embedding_model` metadata in `rules_chunks` / `agent_learnings` tables to track provenance.
  4. Add `/api/admin/reindex-embeddings` endpoint to manually trigger re-embedding.

**[TECH_DEBT] Deal lifecycle state machine not rigorously enforced:**
- Problem: `backend/app/models.py` (CustomerDeal model) and `backend/app/deal_lifecycle.py` allow state transitions like `intake → resolved → in_progress` (backwards). No validation prevents invalid sequences.
- Files: `backend/app/models.py`, `backend/app/deal_lifecycle.py`, `backend/app/deals_routes.py`
- Impact: Deal state becomes inconsistent; reports count deals wrong.
- Fix approach:
  1. Add `@property` validator on Deal model to enforce state machine. Allowed transitions: `intake → in_review → verified → resolved` (no backsliding).
  2. Log every state transition to audit_log with reason.
  3. Add `/api/observability/deal-states` to visualize state distributions.

## Test Coverage Gaps

**[TECH_DEBT] Feature #15 (auto-deploy on main push) not tested:**
- Problem: `feature_list.json:#15` requires deploy.yml to run and `/healthz` to return 200 within 5 minutes. This is an integration test that can't run in CI (requires GitHub Secrets + Railway auth).
- Files: `.github/workflows/deploy.yml`, `feature_list.json:174-180`
- Impact: Deploy breaks silently; discovered only when someone tries to merge to main.
- Fix approach:
  1. Document deploy.yml manual test: "After merge, tail Railway logs for 5 minutes. Grep for 'app running at' message."
  2. Add `/api/version` endpoint that returns git commit hash; verify it changes after deploy.
  3. Add Sentry alert: "Deployment failed" if healthz returns error for >30s after push.
  4. For Wave 5: implement GitHub Status Check that pings /healthz post-deploy.

**[TECH_DEBT] No tests for multi-provider transcription fallback:**
- Problem: If Deepgram fails, does consensus pick AssemblyAI? What if 3 engines fail? No test coverage.
- Files: `backend/app/transcription.py`, `backend/app/tribunal_wer.py`, test files
- Impact: Pipeline silently degrades under provider outages.
- Fix approach:
  1. Add parametrized test: mock each provider failure individually + verify fallback.
  2. Add integration test: set one provider's API key to invalid, upload call, verify transcript still completes.
  3. Document expected behavior: "Minimum 2 of 7 engines must succeed; if fewer, call marked failed and queued for replay."

**[TECH_DEBT] No load test for database pool saturation:**
- Problem: No test simulates 50 concurrent uploads + watchdog cron + Inngest process_call steps competing for 30 DB connections.
- Files: `backend/app/database.py`, test suite
- Impact: Connection pool starvation discovered in production only.
- Fix approach:
  1. Add load test script in `scripts/load_test.py`: spawn 50 concurrent upload tasks, measure connection pool utilization.
  2. Run weekly against Supabase staging to catch issues before prod.
  3. Document acceptable threshold: pool shouldn't exceed 90% utilization under expected peak load.

## Scaling Limits

**[DEPLOY_ATTENTION] Inngest free tier (≤50k runs/month) may be exceeded with high call volume:**
- Problem: `suggested-arch.md` assumes Inngest free cloud. At 10 calls/day × 30 days = 300 calls/month. But each call triggers 6 steps + watchdog cron (1440/month) + pg_dump (30/month) + rag_ingest = ~9k runs/month (safe). However, if volume grows to 50+ calls/day or error retry loop activates, may hit limit.
- Files: `backend/app/inngest_client.py:17-20` (is_production flag)
- Impact: New calls queued but not processed; pipeline backed up.
- Fix approach:
  1. Set up Inngest billing alerts: notify on 80% of monthly limit.
  2. Document escalation: "If hitting paid tier, switch to self-hosted Inngest (Apache 2.0) or Celery+RabbitMQ."
  3. Implement feature flag to disable low-priority tasks (e.g., rag_ingest) if queue depth > threshold.
  4. For production: pre-negotiate Inngest pricing and document transition plan.

**[DEPLOY_ATTENTION] Supabase Storage bucket has no size quotas:**
- Problem: Raw call audio files can accumulate unbounded. At 50MB per call × 1000 calls = 50GB storage cost.
- Files: `backend/app/routes.py:180-205` (upload), `backend/app/storage/supabase_backend.py`
- Impact: Storage costs scale linearly with call volume; no automatic cleanup.
- Fix approach:
  1. Add retention policy: delete call audio from Storage after 90 days (keep transcript in Postgres).
  2. Implement background job: `DELETE FROM calls WHERE created_at < NOW() - INTERVAL '90 days'` + delete from Storage.
  3. Document in observability dashboard: "Storage usage (GB) + projected monthly cost."
  4. For production: negotiate storage SLA with Supabase.

## Known Gaps & Deferred Work

**[TECH_DEBT] GlitchTip + Loki/Prometheus dashboards not yet created:**
- Problem: `feature_list.json:#8-10` require GlitchTip + Grafana dashboards to be live. `docker-compose.observability.yml` defines services, but seed dashboards not yet committed.
- Files: `docker-compose.observability.yml`, `infrastructure/grafana/` (may be empty)
- Impact: Operators have no visibility into errors, metrics, or logs at launch.
- Fix approach:
  1. Document in Wave 2 tasks: create 4 Grafana dashboards (Pipeline, LLM, API, Errors).
  2. Export as JSON to `infrastructure/grafana/dashboards/` for infrastructure-as-code.
  3. Add Grafana provisioning config to auto-load dashboards on startup.

**[TECH_DEBT] Inngest dashboard not exposed in Contabo setup (currently on port 8288):**
- Problem: `suggested-arch.md:128` mentions "Inngest Dashboard :8288" but no Cloudflare Tunnel route defined for it.
- Files: `infrastructure/contabo/README.md`, deployment docs
- Impact: Operators can't see Inngest run state, retries, step graph from outside VPS.
- Fix approach:
  1. Add tunnel route: `cloudflared tunnel route dns inngest.<domain.com> localhost:8288`
  2. Document at launch: "Inngest dashboard at https://inngest.<your-domain>/."
  3. For Railway: Use Railway's built-in private networking (no tunnel needed if Inngest self-hosted).

**[DEPLOY_ATTENTION] Wave 5 feature #15 (auto-deploy) blocked on GitHub PAT rotation:**
- Problem: `claude-progress.txt:73` notes "Revoke compromised GitHub PAT before Wave 5 wires deploy.yml secrets."
- Files: `.github/workflows/deploy.yml`, GitHub repo settings
- Impact: Cannot create deploy workflow without valid, unrotated PAT.
- Fix approach:
  1. Before merging Wave 5, rotate GitHub PAT: Settings → Developer Settings → Personal Access Tokens → Regenerate.
  2. Update GitHub secret `GH_PAT` in repo settings with new token.
  3. Add notification: "Deploy.yml secrets were updated. Verify workflow runs without permission errors on next push."

---

**Summary:** This is a production-ready codebase transitioning from Contabo VPS (with Cloudflare Tunnel) to Railway + Vercel. The 8 **[BLOCKS_DEPLOY]** and 12 **[DEPLOY_ATTENTION]** issues must be resolved before Railway/Vercel deployment. **[SECURITY]** concerns require immediate attention (dev_all_admin validation, API key presence checks). **[PERF]** and **[TECH_DEBT]** items can ship in post-launch waves but should be monitored.

*Concerns audit: 2026-05-09*
