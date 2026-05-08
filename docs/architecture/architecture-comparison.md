# Architecture Comparison — Spec vs Implementation

**Spec doc:** `compliance architecture 2.docx` (Phase 1 Hetzner → Phase 2 AWS plan)
**Codebase:** `backend/` (FastAPI, Python 3.12) + `frontend-v3/` (Next.js 16) — v1 deployed to amina VPS, May 2026
**Date:** 2026-05-06

---

## 1. Verdict at a Glance

| Dimension | Spec | Code | Match |
|---|---|---|---|
| API framework | FastAPI | FastAPI | ✅ |
| Frontend | React (generic) | Next.js 16 + React 19 + shadcn + zustand + TanStack | ✅ (richer) |
| Async pipeline runtime | Celery workers (2 pools) | **Inngest** durable functions | ❌ different choice |
| Message broker | RabbitMQ | **None** (Inngest event bus replaces it) | ❌ different choice |
| Result backend / checkpoints | Redis | **Inngest step memoization** + Postgres `Call` row | ❌ different choice |
| Database | PostgreSQL | **Supabase Postgres** + Alembic + pgvector | ✅ (managed) |
| Object storage | S3 (Hetzner now → AWS later) | **Supabase Storage** (`call-audio` bucket) | ⚠️ different vendor |
| Transcription | AssemblyAI (single) | **5-engine consensus**: Deepgram, AssemblyAI, Speechmatics, Groq, OpenAI, Cohere, Gemini | ✅ (richer) |
| LLM | Claude Haiku (single, cheap) | OpenRouter/Anthropic Sonnet 4.6 default + Gemini Flash first-pass + escalation | ⚠️ richer + costlier |
| Pipeline shape | 5 steps (transcribe → chunk → fanout → aggregate → store) | **6 steps** (download_audio → transcribe → detect_metadata → analyze_checkpoints → score → finalize) | ⚠️ reshaped |
| Rule fan-out | `asyncio.gather` 24 rules × N chunks | Batched checkpoint analyzer + smart agent w/ escalation | ⚠️ different model |
| Error tracking | Sentry | **Not wired** (Loguru only) | ❌ gap |
| Logs | Loguru → Loki | Loguru + custom `/observability` API | ⚠️ no Loki |
| Metrics | Prometheus + Grafana | **None** | ❌ gap |
| Task monitor | Flower | Inngest dashboard `:8288` | ✅ equivalent |
| IaC | Terraform (Hetzner + AWS + Azure dirs) | **None** — Dockerfile + manual VPS deploy | ❌ gap |
| Reverse proxy / SSL | Nginx + Let's Encrypt | Cloudflare Tunnel + direct IP `:9000`/`:8001` | ⚠️ different (works) |
| Env-var abstraction | Mandatory (Pydantic-friendly) | ✅ Pydantic `Settings` w/ `.env` | ✅ |

**Summary:** the implemented system is **architecturally divergent but functionally richer** than the spec. The spec proposes a classic Celery + RabbitMQ + Redis + Sentry/Prometheus stack on Hetzner; the code instead bets on **Inngest + Supabase** to collapse three infrastructure components (broker, checkpoint store, results backend) into one managed service, while spending its complexity budget on **multi-engine STT consensus**, **HITL review workflows**, **RAG/agents**, and **deal-lifecycle features** that aren't in the spec at all.

---

## 2. Layer-by-Layer Comparison

### 2.1 API Layer
- **Spec:** FastAPI receives `POST /analyze` (audio), returns `job_id`, polls `GET /job/:id`.
- **Code:** FastAPI (`app/main.py`) with ~25 routers — `routes`, `agents_routes`, `customers_routes`, `deals_routes`, `directives_routes`, `email_routes`, `flags_routes`, `hitl_routes`, `import_xlsx_routes`, `observability_routes`, `rag_admin_routes`, `rag_routes`, `rejections_routes`, `rules_routes`, `saved_views_routes`, `script_routes`, `tracker_*`. Spec's poll endpoint is one router; code has a full reviewer console behind it.
- **Verdict:** ✅ matches the contract; massively wider surface area.

### 2.2 Async runtime — **the biggest divergence**
- **Spec:** RabbitMQ broker → 2 Celery worker pools (`worker_pipeline` concurrency 2, `worker_analysis` concurrency 8). Durability via `acks_late=True`, `reject_on_worker_lost=True`, Redis checkpoints, exponential backoff (30s → 60s → 120s).
- **Code:** **Inngest** (`inngest>=0.5`). Single durable function `process_call` in `app/workflows/process_call.py` that wraps each pipeline step in `ctx.step.run(...)`. Inngest provides:
  - Event-driven dispatch (`call/uploaded` event from `routes._process_in_background`)
  - Per-step memoization (replay-safe; transcribe step doesn't re-run on retry)
  - Built-in exponential backoff
  - `redispatch_watchdog` cron picks up runs stuck >7min
  - Per-step soft timeouts (`download_audio` 120s, `transcribe` 300s, `analyze_checkpoints` 420s, …)
- **Why it diverges from spec:** Inngest replaces RabbitMQ + Celery + Redis-as-checkpoint with one managed service. Same durability properties (`acks_late` ≈ Inngest's at-least-once delivery; checkpoints ≈ step memoization). Trade-off: vendor coupling vs. zero broker ops.
- **Feature flag:** `settings.use_inngest_pipeline` — when False the legacy in-process asyncio task runs. Flag means migration off Inngest is reversible. Currently False per default; verify production env.

### 2.3 Message broker
- **Spec:** RabbitMQ with two named queues (`pipeline`, `analysis`).
- **Code:** No RabbitMQ. Inngest's hosted event bus carries the `call/uploaded` event.
- **Implication:** the spec's two-pool isolation (analysis flood not blocking transcription) is not implemented because there's only one workflow function. If transcription throughput becomes the bottleneck, this would need either separate Inngest functions per concern, or a pivot back to the spec's two-queue design.

### 2.4 Result backend / checkpoints
- **Spec:** Redis stores Celery results (poll target) + per-job pipeline checkpoints so retries skip transcription.
- **Code:** No Redis. Equivalent guarantees come from:
  - Inngest step memoization → retry skips completed steps.
  - `Call` row columns (`transcript`, `word_data`, `last_step_name`, `last_step_started_at`, `last_step_error`) → forensic checkpoints.
  - `/observability/stuck` route surfaces them.
- **Verdict:** ⚠️ functionally equivalent but the spec's "transcript checkpoint in Redis" is now "transcript on the Call row in Postgres". Slightly heavier writes, simpler ops.

### 2.5 Database
- **Spec:** PostgreSQL with 3 tables: `jobs`, `rule_results`, `audit_log`.
- **Code:** Supabase Postgres (managed). Schema owned by **Alembic** (`backend/alembic/`, `migrations_sql/`); `create_tables_on_startup` is False in prod. Far more than 3 tables — `Call`, `CallCheckpoint`, `Script`, `Customer`, `Deal`, `Directive`, `Flag`, `Rejection`, `AgentLearning` (with pgvector embeddings), HITL claim tables, RAG ingest tables, etc.
- **Verdict:** ✅ matches PostgreSQL choice; managed via Supabase, schema is well beyond the spec's three tables, ownership of migrations is correct.

### 2.6 Object storage
- **Spec:** S3-compatible. Hetzner Object Storage on Phase 1, AWS S3 on Phase 2. Endpoint swap via env var.
- **Code:** **Supabase Storage** bucket `call-audio` (`app/storage.py`, service-role client). No `boto3`, no S3 endpoint env var.
- **Implication:** the spec's "swap endpoint URL → migrate cloud" path is **not** preserved as-is. Migrating off Supabase Storage would require a real code change to swap `supabase.create_client(...)` for an S3 SDK. If Phase 2 (AWS) is on the roadmap, this is the largest portability debt.

### 2.7 Transcription
- **Spec:** AssemblyAI single-engine, speaker diarization, 5-min timeout polling.
- **Code:** Multi-engine consensus tribunal — `assemblyai_transcription.py`, `cohere_transcription.py`, `groq_transcription.py`, `transcription.py` (Deepgram + Gemini), `tribunal_wer.py` (consensus + WER scoring). The pipeline's `_step_transcribe` runs `asyncio.gather` across engines. Deepgram is the default per `requirements.txt` (`deepgram-sdk==3.7.0`).
- **Verdict:** ✅ exceeds spec. Cost will be higher per call but accuracy is the project's competitive moat (see `accuracy_benchmark.py`, `benchmark_audio_native.py`).

### 2.8 LLM rule analysis
- **Spec:** Claude Haiku, JSON-forced output, 24 rules × N chunks fanned out via `asyncio.gather`. Per-call independent retry × 3 with backoff. Failures logged & skipped.
- **Code:** Two modes, gated by `use_agent_analyzer`:
  - **Legacy** (`use_agent_analyzer=False`): batched `analyze_all_checkpoints` runs all rules per call.
  - **Smart Agent** (`use_agent_analyzer=True`): two-tier — Gemini 2.5 Flash first pass, escalate to Claude Sonnet 4.6 when confidence is "low" (`agent_escalation_threshold`). Tool-using agent loop (`app/agent/agent_loop.py`) with up to 8 turns and RAG tool access (`app/agent/rag_tools.py`).
- **Default model:** Sonnet 4.6 (`anthropic_model = "claude-sonnet-4-6"`, `openrouter_model = "anthropic/claude-sonnet-4-6"`) — **not Haiku**. This is intentional (compliance accuracy > token cost), but the spec's cost model assumes Haiku.
- **Verdict:** ⚠️ richer than spec. Watch token spend — Sonnet 4.6 + multi-engine STT + agent loops will overshoot the spec's "$25–35/mo" Hetzner cost envelope dramatically.

### 2.9 Chunking
- **Spec:** AssemblyAI speaker turns primary; 300-token sliding window w/ 50-token overlap fallback (tiktoken). Upgrade path: embedding similarity (text-embedding-3-small) for relevance pre-filter.
- **Code:** Speaker-turn chunking + the upgrade-path embedding system **already implemented** — `app/rag/embed.py`, `app/rag/chunker.py`, `pgvector` + `openai>=1.0` for embeddings, plus full RAG ingest pipelines for rules, gates, LOAs, supplier docs, rejections.
- **Verdict:** ✅ ahead of spec.

### 2.10 Aggregation / scoring
- **Spec:** Dedupe per rule by highest confidence. Final report = score + violations + missing-required-rules list.
- **Code:** `_step_score` → `derive_compliance` (`app/compliance.py`) → score, status, reason. `CallCheckpoint` rows are deleted-and-reinserted under one transaction per `_step_analyze_checkpoints` for retry idempotency.
- **Verdict:** ✅ matches conceptually; richer outputs (deal verdicts, HITL routing, rejection factory).

### 2.11 Durability
- **Spec:**
  - `acks_late=True` (task stays queued until done)
  - `reject_on_worker_lost=True` (OOM-safe)
  - Redis checkpoints
  - Exponential backoff 30s/60s/120s; final failure → mark `failed` + Sentry alert
- **Code:**
  - Inngest at-least-once delivery + step memoization ≈ `acks_late` + Redis checkpoints
  - `redispatch_watchdog` cron sweeps stuck runs >7min
  - Per-step `_STEP_TIMEOUTS` enforce soft caps below the watchdog
  - `last_step_error` persisted on Call for forensics
  - **No Sentry** — failed runs visible only in Inngest dashboard + `/observability` page
- **Verdict:** ✅ durability properties met; ❌ alerting is not.

### 2.12 Observability — the **biggest gap**

| Spec component | Code state | Gap |
|---|---|---|
| Sentry exception tracking | Not wired (no `sentry-sdk` in `requirements.txt`) | ❌ — spec says "set this up first, 30 minutes" |
| Loguru structured logs | ✅ `app/logger.py` | match |
| Loki log aggregation | ❌ no shipping | logs only on local stdout/Docker |
| Prometheus metrics | ❌ no `prometheus-client` instrumentation | no scrape endpoint |
| Grafana dashboards | ❌ | n/a |
| Flower Celery monitor | n/a (no Celery); Inngest dashboard `:8288` covers it | ✅ equivalent |
| Custom `/observability` API + UI | ✅ extra (not in spec) | bonus |

**Recommendation:** Sentry first (lowest effort, highest leverage, matches spec advice). Prometheus is a bigger lift; defer until post-revenue per spec's own "ship now" framing.

### 2.13 Frontend
- **Spec:** "REACT FRONTEND" — generic, polls every 2s.
- **Code:** Next.js 16 (App Router) + React 19 + Tailwind + shadcn + Zustand + TanStack Query + Playwright + Vitest. Far beyond spec scope: HITL reviewer console, agent chat UI, RAG admin, tracker, etc.
- **Verdict:** ✅ wildly exceeds spec.

---

## 3. Features Present in Code but **Not in Spec**

These are real products the team built that the architecture doc doesn't mention. Important to document — they shape both runtime cost and migration complexity.

| Feature | Where | Why it matters for the spec's plan |
|---|---|---|
| **HITL review workflow** | `hitl_routes.py`, idle-claim sweeper in `main.py:_idle_release_loop` | Adds reviewer-state tables, Supabase Auth coupling, periodic background sweep |
| **Smart Agent layer w/ escalation** | `app/agent/`, `agent_loop.py`, `agent_escalation_*` settings | Multi-LLM, tool-using; cost model changes |
| **RAG / pgvector** | `app/rag/`, `rag_admin_routes`, `rag_routes` | Embedding pipeline + vector search; not in spec at all |
| **Deals lifecycle** | `deals_routes.py`, `deal_lifecycle.py`, `deal_verdict.py`, `deals_composite.py` | Domain on top of compliance |
| **Customers / Directives / Rejections / Flags / Rules / Scripts / Glossaries** | named routers + `extraction/` | Each adds tables + UI |
| **Tracker XLSX import/export** | `import_xlsx_routes.py`, `tracker_*.py` | Adds `openpyxl` + ETL paths |
| **Vulnerable-customer detection** | `extraction/vulnerability.py`, flag `vulnerable_detection_enabled` | Two-stage detector (regex + LLM) |
| **Pricing-mismatch detection** | `extraction/pricing.py`, flag `pricing_mismatch_enabled` | Regex extractor |
| **Field-source provenance tracking** | `field_sources.py` | Tracks which engine/agent set each field |
| **Multi-engine STT tribunal** | `tribunal_wer.py`, 5+ transcription modules, `accuracy_benchmark.py` | Real cost driver |

---

## 4. Items in Spec **Not Yet Implemented**

| Spec item | Status | Effort |
|---|---|---|
| Sentry SDK | ❌ missing | S — `pip install sentry-sdk[fastapi]` + DSN env var |
| Prometheus `/metrics` endpoint | ❌ missing | M — instrument FastAPI + Inngest function timings |
| Grafana + Loki | ❌ missing | M — Docker Compose + Promtail + dashboards |
| Terraform (Hetzner) | ❌ missing | M — `hetzner/main.tf`, `dns.tf`, firewall, SSH keys |
| Terraform (AWS / Azure) | ❌ missing | L — only do when migrating to Phase 2 |
| API Gateway (rate limit / auth / SSL) | ⚠️ partial — Cloudflare Tunnel handles SSL; rate limiting + structured auth not visible | S–M |
| GitHub Actions CI/CD `docker compose pull && up -d` | unknown — no `.github/workflows` listed in repo root | S |
| AWS Phase 2 migration | ❌ not started; **Supabase Storage swap is real code change**, not env-var change | L |

---

## 5. Cost Model — Reality Check

The spec's Phase 1 estimate is **~$25–35 / month** (single Hetzner cx41 hosts everything: VPS + Postgres + RabbitMQ + Redis + S3-equivalent).

Code's actual cost surface (rough monthly, per-call costs not included):

| Line | Cost |
|---|---|
| Amina VPS (FastAPI + frontend + Inngest dev server) | ~$25–40 |
| Supabase (Postgres + Storage + Auth) | $0 free tier → **$25/mo** Pro tier likely required at any volume |
| Inngest (managed) | $0 free tier → meters by run; **$20–50/mo** typical |
| Multi-engine STT (Deepgram + AssemblyAI + Speechmatics + Groq) | **per call**; can dwarf everything else |
| LLM (Sonnet 4.6 default + Gemini Flash) | **per call**; Sonnet is ~12× Haiku |
| OpenAI embeddings (RAG) | per ingest |

**Net:** the spec's "ship for $25/mo" target is realistic only if STT is single-engine (e.g., Deepgram only) and the analyzer is held on Gemini Flash / Haiku. With current defaults (Sonnet + tribunal), per-call cost is the dominant line, not infra.

---

## 6. Recommendations (in priority order)

> **Warning:** items 1–2 are sensitive; confirm before changing production behavior.

1. **Wire Sentry** (`sentry-sdk[fastapi]`) — spec's #1 ops priority, 30 min to add, captures everything Inngest dashboard misses (FastAPI request errors, frontend issues if you add the JS SDK).
2. **Decide on Inngest vs Celery long-term.** Inngest works and matches durability properties, but the spec's cost & migration model assumed self-hosted brokers. If Phase 2 (AWS) materializes, Amazon MQ + Celery is closer to spec; staying on Inngest means accepting that vendor at all phases.
3. **Document the Supabase Storage choice.** Spec assumes S3-compatible swap-by-env-var; code uses Supabase SDK. Either (a) abstract the storage layer behind a small interface so swapping to S3 is one file, or (b) explicitly accept Supabase as Phase-2 too.
4. **Reconcile cost model.** Either lower analyzer/STT defaults (Haiku + Deepgram-only) to fit Phase-1 budget, or update the spec's cost section to match reality.
5. **Add Prometheus `/metrics`** — even just queue depth + per-step latency histograms. Inngest dashboard shows runs, not aggregate health.
6. **Terraform Phase 1.** Even if you stay on Amina, codifying VPS + DNS + firewall in Terraform is the spec's cleanest deliverable and unlocks the Phase-2 migration story you're selling.
7. **Consider a second Inngest function for analysis-only retries** to recover the spec's two-pool isolation if transcription contention shows up.

---

## 7. One-Line Summary

> The implementation **honored the spec's contract** (FastAPI + Postgres + S3-shape + durable async + observability hooks) but **swapped the Celery/RabbitMQ/Redis trio for Inngest+Supabase**, **added five-engine STT consensus**, and **built ~10× the domain features** (HITL, deals, RAG, agents). The remaining gaps are observability (Sentry/Prometheus), IaC (Terraform), and a portability story for Supabase Storage if AWS Phase 2 happens.

---

## Phase 1 — Enterprise Hardening Inject — STATUS

| Wave | Deliverables | PR | Status |
|---|---|---|---|
| Wave 1 | CI workflows (test/coverage/touched-fns-gate), audit_log + failed_jobs migrations + writers, Contabo IaC + Cloudflare DNS via OpenTofu | n/a (predates PR convention) | ✅ shipped |
| Wave 2 | GlitchTip self-hosted, Sentry SDK (backend + frontend), `/metrics`, `/healthz`, `/readyz`, Prom + Loki + Promtail + Grafana with 4 seed dashboards, JSON logger | #1 | ✅ shipped |
| Wave 3 | StorageBackend ABC (Supabase + S3/MinIO), `POST /api/calls/{id}/reanalyze` + `process_call_reanalyze` Inngest fn, ReanalyzeButton, pg_dump_nightly cron, restore_drill.sh, durability.md | #2 | ✅ shipped |
| Wave 4 | Embedding pre-filter, A/B parity harness, cost-optimization runbook, DEV_ALL_ADMIN flag | #3 | ✅ shipped (defaults still off pending A/B run) |
| Wave 5 | deploy.yml SSH workflow, branch protection runbook + apply script, consolidated ops runbook | #4 | this PR |

### Spec coverage vs `compliance architecture 2.docx`

| Spec § | Status |
|---|---|
| 2.1 API (Prometheus + healthz) | ✅ Wave 2 |
| 2.2-2.4 Broker / Celery / Redis | ⚠ Skipped — Inngest replaces (documented in this file) |
| 2.5 audit_log + failed_jobs | ✅ Wave 1 |
| 2.6 Object storage portability | ✅ Wave 3 |
| 3.1 Transcribe (5-engine tribunal) | ✅ pre-existing (ahead of spec) |
| 3.2 Embedding similarity pre-filter | ✅ Wave 4 (flag-gated) |
| 4 Durability semantics | ✅ Wave 3 (durability.md maps to Inngest) |
| 5.1 Sentry / GlitchTip | ✅ Wave 2 |
| 5.2 Loguru → Loki | ✅ Wave 2 |
| 5.3 Prometheus + Grafana | ✅ Wave 2 |
| 5.4 Flower | ⚠ Skipped — Inngest dashboard is equivalent |
| 7 Deployment | ✅ Wave 5 (deploy.yml + Contabo runbook) |
| 8 IaC | ✅ Wave 1 (Cloudflare DNS via OpenTofu; Contabo VPS via SSH+Compose runbook) |
| 9 Env vars | ✅ Wave 2-5 (Pydantic Settings + .env.example) |
| 10 Cost | ✅ Wave 4 (flags ready; A/B gate before flip) |
| Replay (§2.5) | ✅ Wave 3 |

### Open follow-ups (post-Phase-1)

- Run Wave 4 A/B parity sample (≥50 calls); flip flags to True after parity ≥ 98%.
- Two-pool Inngest split (analysis vs pipeline) — defer until starvation measured.
- AWS migration (Phase 2) — storage abstraction is the only prep already in place.
- SOC2 / ISO27001 cert work — `audit_log` is the prereq, not the cert itself.
- Frontend Sentry replay sessions — privacy review needed before enabling.
