# Enterprise Hardening Inject — Compliance v1 → v1.1

**Author:** brainstormed via Claude Code (superpowers:brainstorming)
**Date:** 2026-05-06
**Source spec compared against:** `compliance architecture 2.docx` (Phase 1 Hetzner → Phase 2 AWS plan)
**Companion doc:** `architecture-comparison.md` (root of repo)
**Related repos:**
- Deployed: `/Users/gomaa/Documents/Compliance` (tracker branch, single squashed commit)
- Original dev: `/Users/gomaa/Documents/Compliance-Agent` (full git history)
- Remote: `github.com/ArcadeTechLTD/compliance-agent`

---

## 1. Goal

Take the architectural intent of `compliance architecture 2.docx` — error tracking, log aggregation, metrics, IaC, audit trail, durable backups, cost control, replayability, portability, observability dashboards — and inject it into the live Compliance v1 stack **without** retrofitting Celery/RabbitMQ/Redis (Inngest replaces them) and **without** new paid SaaS dependencies. Result: same product, enterprise-grade ops + heavy-load posture.

## 2. Non-Goals

- No replacement of Inngest with Celery + RabbitMQ + Redis. Inngest's at-least-once delivery + step memoization meet the spec's durability semantics.
- No AWS / Azure migration. Storage abstraction is **portability prep**, not migration.
- No new paid SaaS subscriptions beyond what is already running. All free tier or self-hosted.
- No new product features beyond `POST /calls/:id/reanalyze` (replay endpoint, which is itself in the source spec).
- No refactor of unrelated code. Existing patterns preserved.
- No removal of any existing feature (HITL, RAG, agents, deals, tracker, etc.).

## 3. Success Criteria

1. Unhandled exception in FastAPI or Inngest function appears in self-hosted GlitchTip within 30 seconds, tagged with `job_id` / `call_id` and request context.
2. Grafana exposes four dashboards on Loki + Prometheus: **Pipeline** (per-step duration, throughput), **LLM** (call rate, latency, escalation rate, token cost), **API** (RPS, p50/p95/p99, error rate per route), **Errors** (top exception types, frequency, last seen).
3. `audit_log` row written on every `/upload`, `/reanalyze`, HITL claim/release, rule change, and admin action. `failed_jobs` row written on every Inngest run that exhausts retries.
4. `POST /calls/:id/reanalyze` returns a fresh verdict using the stored transcript with **zero re-transcription cost**, completing in ≤30 s.
5. With tiered LLM (`use_agent_analyzer=True`, Gemini Flash first → Sonnet escalate) + embedding pre-filter, mean LLM cost per call drops ≥5× on calls where first-pass agent confidence ≥ medium, with verdict parity ≥98% vs. prior Sonnet baseline on a 50-call A/B sample.
6. Daily `pg_dump` cron writes encrypted dated tarball to Supabase Storage `backups/` bucket; 7-day retention enforced; one full restore drill into a scratch DB completed and documented.
7. `tofu plan` against the live amina VPS shows zero diff (state matches reality).
8. `git push` to `main` triggers GitHub Actions: `test.yml` (pytest + vitest + playwright) and `coverage.yml` (≥60% line coverage on touched files) run as **required checks**; `deploy.yml` SSHes amina and runs `docker compose pull && up -d` in ≤5 min on green.
9. Branch protection on `main` requires `test.yml` and `coverage.yml` to pass before merge.
10. `verification-before-completion` skill extension refuses task closure when modified `*.py` / `*.ts` files in the diff lack a corresponding test file change in the same branch.

## 4. Architecture

### 4.1 Runtime topology (after inject)

```
┌────────────────────────────────────────────────────────────────┐
│  GitHub PR                                                     │
│   ├── push triggers test.yml + coverage.yml (required checks)  │
│   ├── reviewer fills PR template (touched fns ↔ tests)         │
│   └── merge to main → deploy.yml → SSH amina VPS               │
└──────────────┬─────────────────────────────────────────────────┘
               │
┌──────────────▼─────────────────────────────────────────────────┐
│  Amina VPS (Hetzner-class)                                     │
│  Docker Compose (existing + 6 new services):                   │
│    compliance-backend  (FastAPI 8001)                          │
│    compliance-frontend (Next.js 9000)                          │
│    glitchtip          (Sentry-API 8080)         NEW            │
│    prometheus         (9090)                    NEW            │
│    grafana            (3001)                    NEW            │
│    loki               (3100)                    NEW            │
│    promtail           (Docker socket reader)    NEW            │
│    pg_dump_cron       (alpine + cron sidecar)   NEW            │
│    minio (optional)   (S3 API 9001) — only when STORAGE_BACKEND=s3
└────────────────────────────────────────────────────────────────┘
                                │
                                ▼ writes
┌────────────────────────────────────────────────────────────────┐
│  Supabase                                                      │
│   Postgres: existing tables + audit_log + failed_jobs   NEW    │
│   Storage:  call-audio bucket + backups/ bucket         NEW    │
└────────────────────────────────────────────────────────────────┘
                                │
                                ▼ event
┌────────────────────────────────────────────────────────────────┐
│  Inngest cloud (free tier)                                     │
│   process_call          (existing)                             │
│   redispatch_watchdog   (existing)                             │
│   pg_dump_nightly cron  NEW (schedule, no infra)               │
└────────────────────────────────────────────────────────────────┘
                                │
                                ▼ scrape / ship
┌────────────────────────────────────────────────────────────────┐
│  Observability flow                                            │
│   FastAPI /metrics  ←  Prometheus scrape (15 s)                │
│   Loguru JSON →  stdout  →  Promtail  →  Loki                  │
│   Grafana dashboards: Pipeline / LLM / API / Errors            │
│   GlitchTip captures uncaught exceptions w/ job_id tag         │
└────────────────────────────────────────────────────────────────┘
```

### 4.2 Layer-by-layer mapping (spec doc § → inject)

| Spec § | Spec layer | Inject |
|---|---|---|
| 2.1 API | FastAPI | Add `/metrics` (Prometheus), `/healthz`, `/readyz`. Mount Sentry SDK pointed at GlitchTip URL. |
| 2.2 Broker | RabbitMQ | **Skipped** — Inngest replaces. Documented in `architecture-comparison.md`. |
| 2.3 Task queue | Celery | **Skipped** — Inngest functions cover. Two-pool isolation deferred until starvation measured. |
| 2.4 Result + checkpoints | Redis | **Skipped** — Inngest step memoization + Postgres `Call` row cover. |
| 2.5 DB | Postgres + 3 tables | `audit_log` **already shipped** (Alembic migration `497bd38e5551`, with tamper-evident hash chain in `app/audit.py`). This inject (a) adds `failed_jobs` (Inngest exhaustion forensics), and (b) expands `record_audit()` call coverage across mutating routers (currently only 3 files use it). |
| 2.6 Object storage | S3 / Hetzner Object Storage (per source doc) | Production runs on Contabo VPS + Supabase Storage; `S3` is portability target only. Refactor `app/storage.py` into `StorageBackend` ABC with `supabase_backend.py` (default) + `s3_backend.py` (boto3) impls. Backend selected by `STORAGE_BACKEND` env var. |
| 3.1 Transcribe | AssemblyAI | **Skipped** — code already 5-engine tribunal, ahead of spec. |
| 3.2 Chunk + upgrade path | Embedding similarity | **Implemented** — wire pgvector cosine sim chunk×rule before LLM fan-out (flag `embedding_prefilter_enabled`, default False until A/B parity proven). |
| 3.3 Fan-out | asyncio.gather + per-rule retry | Verify per-rule `tenacity` retry exists in `checkpoint_analyzer.py`; add if missing. |
| 3.4 Aggregate | Highest-confidence dedupe + missing-required list | Verify in `compliance.derive_compliance`; spec output if absent. |
| 4 Durability | acks_late + checkpoints + backoff | **Already met** by Inngest. Document in repo `docs/durability.md`. |
| 5.1 Sentry | Errors | **GlitchTip self-hosted** (Sentry-API compatible). FastAPI + frontend SDKs. |
| 5.2 Loguru → Loki | Structured logs | Promtail tail Docker stdout → Loki. Existing Loguru JSON unchanged. Search by `job_id` in Grafana. |
| 5.3 Prometheus + Grafana | Metrics | `prometheus-fastapi-instrumentator` + custom counters/histograms in `app/observability_metrics.py`. Four seed dashboards as JSON in `infrastructure/grafana/dashboards/`. |
| 5.4 Flower | Celery monitor | **Skipped** — Inngest dashboard equivalent. |
| 7 Deployment | VPS + Docker Compose | Existing. Add `docker-compose.observability.yml` + `docker-compose.backup.yml` overlays. |
| 8 IaC | Terraform | **OpenTofu** (OSS Terraform fork) — `infrastructure/contabo/{versions,variables,dns}.tf`. Cloudflare DNS only (Contabo provider too thin for full VM IaC); Contabo VPS lifecycle = SSH + Docker Compose runbook in `infrastructure/contabo/README.md`. |
| 9 Env vars | Pydantic Settings | Already aligned. Add `sentry_dsn`, `storage_backend`, `prometheus_enabled`, `embedding_prefilter_enabled`, `coverage_threshold`. |
| 10 Cost | Hetzner $25–35/mo | Hit by flipping defaults: `use_agent_analyzer=True`, `embedding_prefilter_enabled=True` after A/B passes. |
| Replay (§2.5) | Re-run on stored transcript | `POST /calls/:id/reanalyze` — re-runs steps 4 (analyze_checkpoints), 5 (score), 6 (finalize) only. |

### 4.3 Testing layer (cross-cuts every deliverable)

The source spec's testing layer is implicit (Phase 2 SOC2 mentioned, but no tests called out). The inject **explicitly** builds testing into the workflow:

- **Discipline:** existing `superpowers:test-driven-development` skill remains the rigid per-test law (RED → GREEN → REFACTOR, Iron Law).
- **CI enforcement:** `.github/workflows/test.yml` runs pytest + vitest + playwright (label-gated) on every push and PR. **Currently absent — no `.github/` directory in repo.**
- **Coverage gate:** `.github/workflows/coverage.yml` runs `pytest-cov` with `--cov-fail-under=60` (ratchet to 75 → 85 over time).
- **PR template:** `.github/pull_request_template.md` with explicit "Functions touched" + "Tests added" sections.
- **Per-task gate:** extend `superpowers:verification-before-completion` skill with one new check — diff modified prod files against test files in the same branch; refuse completion when any modified `*.py` or `*.ts` file outside test/migration directories has no corresponding test-file edit. Implementation: lightweight bash check, ~20 LOC.
- **Branch protection:** GitHub repo settings — `main` requires `test.yml` + `coverage.yml` green before merge; require linear history; no force-push.

This is the answer to "inject another layer for testing each code touched." It is enforcement scaffolding around existing TDD discipline, not a replacement skill.

## 5. Components / file inventory

### 5.1 New files

| Path | Purpose |
|---|---|
| `.github/workflows/test.yml` | pytest + vitest + playwright (label-gated) |
| `.github/workflows/coverage.yml` | `pytest-cov --cov-fail-under=60` |
| `.github/workflows/deploy.yml` | SSH amina VPS, `docker compose pull && up -d` on `main` push |
| `.github/pull_request_template.md` | touched fns / tests checkbox |
| `infrastructure/contabo/versions.tf` | OpenTofu + Cloudflare provider versions |
| `infrastructure/contabo/dns.tf` | Cloudflare DNS A record for `compliance.<domain>` (only Terraform-managed resource) |
| `infrastructure/contabo/variables.tf` | `cloudflare_*` + `vps_ipv4` (manually maintained) |
| `infrastructure/contabo/README.md` | Contabo VPS SSH + Docker Compose runbook + Cloudflare DNS import instructions |
| `docker-compose.observability.yml` | Loki + Promtail + Prom + Grafana + GlitchTip |
| `docker-compose.backup.yml` | `pg_dump_cron` sidecar (alpine + cron) |
| `infrastructure/grafana/dashboards/pipeline.json` | per-step duration, throughput |
| `infrastructure/grafana/dashboards/llm.json` | LLM call rate, latency, escalation rate |
| `infrastructure/grafana/dashboards/api.json` | RPS, p50/p95/p99, error rate |
| `infrastructure/grafana/dashboards/errors.json` | GlitchTip top exceptions, last seen |
| `infrastructure/promtail/config.yml` | Docker stdout → Loki |
| `infrastructure/prometheus/prometheus.yml` | scrape `compliance-backend:8001/metrics` |
| `backend/alembic/versions/<rev>_audit_log_failed_jobs.py` | `audit_log` + `failed_jobs` migration |
| `backend/app/observability_metrics.py` | Prometheus counters + histograms |
| `backend/app/storage/__init__.py` | `StorageBackend` ABC + factory |
| `backend/app/storage/supabase_backend.py` | refactored from existing `storage.py` |
| `backend/app/storage/s3_backend.py` | boto3 impl (works against MinIO + AWS S3) |
| `backend/app/replay.py` | reanalyze logic — re-runs pipeline steps 4–6 on stored transcript |
| `backend/scripts/pg_dump_to_storage.py` | backup runner invoked by Inngest cron |
| `backend/tests/test_storage_backend.py` | ABC contract + Supabase impl + S3 impl |
| `backend/tests/test_replay.py` | replay endpoint behavior |
| `backend/tests/test_audit_log.py` | every mutating route writes audit row |
| `backend/tests/test_failed_jobs.py` | Inngest exhaustion → row |
| `backend/tests/test_observability_metrics.py` | metric registration + values |
| `backend/tests/test_embedding_prefilter.py` | cosine sim selects rules above threshold |
| `frontend-v3/src/sentry.client.ts` | browser SDK pointed at GlitchTip |
| `docs/durability.md` | document Inngest's mapping to spec's durability semantics |
| `scripts/restore_drill.sh` | one-shot scratch-DB restore from latest backup |

### 5.2 Modified files

| Path | Change |
|---|---|
| `backend/app/main.py` | mount `/metrics`, `/healthz`, `/readyz`; init Sentry SDK pointing at GlitchTip URL |
| `backend/app/config.py` | add `sentry_dsn`, `storage_backend`, `prometheus_enabled`, `embedding_prefilter_enabled`, `coverage_threshold`, `s3_endpoint`, `s3_access_key`, `s3_secret_key`, `s3_bucket` |
| `backend/app/routes.py` | call `record_audit()` on `/upload` + `/reanalyze`; emit metrics |
| `backend/app/hitl_routes.py` | call `record_audit()` on claim/release/lock-override |
| `backend/app/rules_routes.py` | call `record_audit()` on rule create/update/delete |
| `backend/app/workflows/process_call.py` | wrap each step in metrics decorator + `logger.bind(job_id=…, step=…)` |
| `backend/app/workflows/redispatch_watchdog.py` | write to `failed_jobs` on retry exhaustion |
| `backend/app/checkpoint_analyzer.py` | embedding pre-filter before LLM fan-out (flag-gated) |
| `backend/app/agent/agent_loop.py` | `agent_escalation_threshold` already exists — flip default + tune |
| `backend/requirements.txt` | + `sentry-sdk[fastapi]==2.x`, `prometheus-fastapi-instrumentator==7.x`, `boto3==1.x`, `pytest-cov==5.x` |
| `backend/Dockerfile` | add `HEALTHCHECK CMD curl -f localhost:8001/healthz` |
| `frontend-v3/package.json` | + `@sentry/nextjs` |
| `frontend-v3/next.config.mjs` | wrap with Sentry's `withSentryConfig` |
| `docker-compose.yml` | reference `docker-compose.observability.yml` + `docker-compose.backup.yml` overlays |

## 6. Data flow changes

### 6.1 Audit trail
Every mutating route boundary calls `record_audit(actor, action, resource_id, ip, payload_hash)`. Writes one row to `audit_log`. Read route `/observability/audit?since=…&action=…` paginated, reviewer-only. Row count expected: ~10–50 per call ingestion (upload + claim + verdict + finalize), ~500 / day at current volume.

### 6.2 Failed jobs
`redispatch_watchdog` cron and Inngest's exhausted-retry handler both write to `failed_jobs(call_id, last_step, attempts, last_error, exhausted_at)`. Reviewer UI surfaces them in `/observability/stuck`. Replay endpoint can be invoked from a failed job row.

### 6.3 Replay path
```
POST /calls/:id/reanalyze
   → load Call row (transcript + word_data + script_id required, else 422)
   → emit Inngest event call/reanalyze (separate from call/uploaded)
   → workflow runs steps 4 (analyze_checkpoints) → 5 (score) → 6 (finalize)
   → audit_log row written; previous CallCheckpoint rows deleted-and-replaced (existing idempotency)
   → returns 202 with new run_id; client polls /calls/:id for new verdict
```

### 6.4 Embedding pre-filter (flag-gated)
```
inside _step_analyze_checkpoints:
   if embedding_prefilter_enabled:
      embed each chunk via text-embedding-3-small (already in code via app/rag/embed.py)
      embed each rule via same model (cached at startup — rules change rarely)
      cosine_sim(chunk, rule) > threshold (0.55 default) → include in fan-out
      else → skip (rule not relevant to this chunk)
   else:
      original behavior (all chunks × all rules)
```
Cuts LLM call count by 50–80% on typical calls.

### 6.5 Tiered LLM (config flip + tune)
`use_agent_analyzer` flips True. `agent_escalation_threshold` set to `low` initially (escalate on low confidence), dial to `medium` after parity proven. Code already exists in `app/agent/`.

### 6.6 Storage abstraction
```
old:
   from app.storage import upload_audio, download_audio, signed_url
new:
   from app.storage import storage_backend
   storage_backend.upload(local_path, key)
   storage_backend.download(key) -> local_path
   storage_backend.signed_url(key, ttl) -> str
```
Backend factory:
```python
def get_backend() -> StorageBackend:
    if settings.storage_backend == "s3":
        return S3Backend(...)
    return SupabaseBackend(...)
```
Existing call sites updated to use the ABC. Default = SupabaseBackend; behavior byte-identical.

## 7. Error handling

- **GlitchTip availability:** if GlitchTip is down, Sentry SDK buffers and retries; never blocks the request path. SDK is configured with low timeout (1 s) and async transport.
- **Prometheus down:** `/metrics` endpoint always available locally; Prometheus scrape failures show in Grafana as gaps in graphs, not request errors.
- **Loki / Promtail down:** logs continue to stdout; aggregation lags but no log loss because Promtail tails Docker JSON file logs (persistent on disk, default 10 MB × 3 file rotation).
- **Backup runner failure:** Inngest cron retries; failure after retries writes to `failed_jobs` and emits GlitchTip event. Restore drill script independent of runner.
- **Embedding pre-filter false negative (drops a relevant chunk):** flag-gated; A/B parity gate before flip; rollback = flag off.
- **Tiered LLM verdict drift:** same A/B gate; tunable threshold; rollback = flag off.
- **`StorageBackend` refactor regression:** `test_storage_backend.py` covers signed-URL TTL contract, multipart upload, content-type round-trip; both impls run against same suite.
- **CI flake:** playwright label-gated to avoid noise on every PR; `test.yml` retries playwright once before failing.
- **OpenTofu state drift:** committed lock file + `tofu plan` in CI on `infrastructure/**` changes; `tofu import` first, never `tofu apply` from a clean state against live infra.

## 8. Testing strategy

Per `superpowers:test-driven-development`: every new function gets a failing test first.

- **Unit tests (pytest):** every new file in `backend/app/storage/`, `backend/app/replay.py`, `backend/app/observability_metrics.py`, plus migration tests for `audit_log` / `failed_jobs`. Embedding pre-filter contract tested with synthetic chunks.
- **Integration tests (pytest):** `/upload` → audit row; `/reanalyze` → fresh verdict no transcription cost; `redispatch_watchdog` exhaustion → `failed_jobs` row; storage backend swap (Supabase ↔ MinIO) round-trip.
- **Frontend unit (vitest):** Sentry SDK init guard, replay button visibility, audit log paginator.
- **E2E (playwright):** upload → wait → replay → reverdict. Only on PRs labeled `e2e`.
- **Smoke (post-deploy):** `deploy.yml` curls `/healthz`, `/readyz`, `/metrics` on amina; rolls back on failure.
- **Restore drill:** `scripts/restore_drill.sh` runs against scratch DB monthly; documented in `docs/durability.md`.
- **A/B parity (manual once):** 50-call sample, verdicts compared between old and new (embedding + tiered LLM); ≥98% parity required to flip flags in prod.

Per-task gate (cross-cutting):
- Every plan task lists `touched_functions:` in its TDD plan output.
- `verification-before-completion` skill addition refuses task done unless every modified prod file in branch diff has matching test edit.
- CI's `coverage.yml` enforces ≥60% line coverage on touched files.

## 9. Migration plan / waves

Each wave = single PR, reversible.

**Wave 1 — foundation (parallel, no runtime risk)**
- W1a: `.github/workflows/test.yml` + `coverage.yml` + PR template
- W1b: `infrastructure/contabo/*.tf` Cloudflare DNS scaffolding + Contabo runbook README (`tofu import` DNS record, plan = 0 diff before merge)
- W1c: Alembic migration `audit_log` + `failed_jobs`

**Wave 2 — observability (capture-only, no behavior change)**
- W2a: GlitchTip Docker service + `sentry-sdk` wiring backend + frontend
- W2b: `/metrics` endpoint + `prometheus-fastapi-instrumentator` + `app/observability_metrics.py`
- W2c: `docker-compose.observability.yml` (Loki + Promtail + Prom + Grafana) deploy + 4 seed dashboards
- W2d: `record_audit()` calls in mutating routes; `failed_jobs` writer in `redispatch_watchdog`

**Wave 3 — durability + portability**
- W3a: `StorageBackend` ABC refactor (Supabase impl byte-identical; tests prove)
- W3b: `app/replay.py` + `POST /calls/:id/reanalyze` + frontend replay button
- W3c: `pg_dump_cron` sidecar + Inngest `pg_dump_nightly` cron + first restore drill

**Wave 4 — cost optimizers (flag-gated, A/B-guarded)**
- W4a: embedding pre-filter behind `embedding_prefilter_enabled` flag (default False)
- W4b: tiered LLM default flip (`use_agent_analyzer=True`)
- A/B 50-call parity sample → flip prod flags only on ≥98% parity

**Wave 5 — deploy + protection**
- W5a: `deploy.yml` SSH workflow with deploy-only key
- W5b: branch protection on `main`: require `test.yml` + `coverage.yml`; linear history; no force-push
- W5c: documentation pass — `docs/durability.md`, `docs/runbook.md`, `docs/architecture-comparison.md` (already exists)

## 10. Risks + mitigations

| Risk | Mitigation |
|---|---|
| OpenTofu state drift vs live VPS | `tofu import` first; `tofu plan` must show 0 diff before merging W1b |
| Embedding pre-filter false negative → false-pass on rule | Flag-gated, A/B 50-call parity gate, rollback = flag off |
| Tiered LLM verdict drift | Same A/B gate; threshold tuneable; rollback = flag off |
| `StorageBackend` refactor breaks signed URLs | Public method signatures byte-identical; integration test covers TTL + content-type |
| `pg_dump` backup unverified | Mandatory restore drill into scratch DB before declaring W3 done |
| GlitchTip self-host RAM (~512 MB) | Budget VPS spec; fallback = Sentry free tier (5k errors/mo) if VPS pressure |
| GitHub Actions deploy SSH secret leak | Deploy-only key; restricted command (`compose pull && up -d` only); rotated quarterly |
| CI runtime cost on free tier (2k min/mo private) | Playwright label-gated; pytest+vitest only on every PR; cache pip + npm |
| Audit log row volume | Index on `(actor, created_at)` + `(action, created_at)`; partition by month after 6 months |
| Replay endpoint abused (DoS) | Rate-limited to 1/min/call; HITL-role required |
| Brand new test files no fixtures | `conftest.py` + `tests/fixtures/` already present; reuse existing patterns |

## 10.1 Wave-5 prerequisite — rotate compromised PAT

A GitHub Personal Access Token was leaked into the local `git remote -v` output during brainstorming and is therefore in the conversation transcript. **Before Wave 5** (`deploy.yml` adds repo SSH/PAT secrets), the leaked token must be revoked at https://github.com/settings/tokens, a fresh token issued with minimum scopes, and the local remote URL replaced with `https://github.com/ArcadeTechLTD/compliance-agent.git` (no embedded credentials; auth via `gh auth login` keychain). This is a hard prereq, not optional.

## 11. Out of scope (explicitly deferred)

- Two-pool Inngest split (analysis vs pipeline) — defer until starvation measured
- AWS / Azure migration — Phase 2; storage abstraction is the only prep
- Cloudflare WAF rules — defer until traffic warrants paid plan
- API Gateway as separate service — Cloudflare Tunnel covers SSL + tunneling
- SOC2 / ISO27001 certification work — `audit_log` is the prereq, not the cert
- Frontend Sentry replay sessions — privacy review needed before enabling

## 12. Open questions

1. **GlitchTip persistence:** SQLite in volume vs Postgres backend? Default SQLite for simplicity; revisit if event volume > 10k/day.
2. **Backup encryption key location:** Supabase Vault vs sealed env var? Recommend Supabase Vault for rotation story.
3. **Cloudflare Tunnel vs direct IP:** spec uses Nginx + Let's Encrypt; current uses Cloudflare Tunnel. Keep Cloudflare; add Caddy as reverse-proxy fallback only if tunnel reliability becomes an issue.
4. **Frontend GlitchTip vs separate Sentry frontend project:** start with one project, separate if signal-to-noise issue.
5. **Coverage threshold ratchet schedule:** 60 → 75 → 85 over 3 milestones, or measure first then ratchet?

## 13. Cost forecast (monthly, USD)

| Line | Cost |
|---|---|
| Amina VPS (existing) | $25–40 |
| Supabase Free tier (or Pro $25 if PITR chosen later) | $0–25 |
| Inngest free tier (≤50k runs/mo) | $0 |
| GlitchTip self-hosted | $0 (RAM cost absorbed by VPS) |
| Loki + Promtail + Prom + Grafana | $0 (RAM cost absorbed by VPS) |
| GitHub Actions (private, free 2k min/mo) | $0 |
| OpenTofu | $0 |
| Per-call: STT tribunal + LLM + embeddings | unchanged (pay-per-use, dominant variable cost) |
| **New $/mo from this inject** | **$0** |

VPS RAM headroom check: existing services + 5 new (GlitchTip 512 MB, Prom 256 MB, Grafana 256 MB, Loki 128 MB, Promtail 64 MB) ≈ +1.2 GB. Budget VPS class needs ≥4 GB to absorb comfortably.

## 14. Acceptance checklist (final review gate)

- [ ] All 10 deliverables shipped behind feature flags or as additive code
- [ ] CI required-checks green on `main`
- [ ] Branch protection enforced
- [ ] Every modified prod file in branch has corresponding test edit
- [ ] One restore drill completed and documented
- [ ] One A/B parity sample (50 calls) completed before W4 flag flip
- [ ] `tofu plan` against live = 0 diff
- [ ] Grafana dashboards show non-zero data for ≥24 h
- [ ] GlitchTip captures a deliberately raised exception in staging
- [ ] Documented in `docs/durability.md`, `docs/runbook.md`
