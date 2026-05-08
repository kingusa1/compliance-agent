COMPLIANCE CALL ANALYSIS SYSTEM
Architecture, Tools & Deployment Guide
Full Production-Ready Stack  ·  Phase 1 (Contabo + Supabase + Inngest) → Phase 2 (AWS / self-host portability)

This is the working spec — corrected to reflect what we are actually building
across Waves 1–5. The original v0 proposal had RabbitMQ + Celery + Redis + Sentry
SaaS + Hetzner. v1 corrects all of that: managed durability via Inngest, managed
Postgres + Storage via Supabase, OSS observability via GlitchTip + LGTM-lite,
Contabo VPS instead of Hetzner.

1. System Overview
The system ingests recorded sales / compliance audio calls, transcribes them
via a multi-engine consensus tribunal, chunks the transcript by speaker turns,
runs each chunk against a catalog of compliance rules using a tiered LLM
(Gemini Flash first → Claude Sonnet 4.6 escalate), routes the verdict through
a Human-In-The-Loop reviewer console, and rolls up per-deal outcomes. Output:
structured compliance verdict per call + deal-level rollup, surfaced to
reviewers in the Next.js console and persisted in Supabase Postgres for replay
and audit.

┌─────────────────────────────────────────────────────────────────┐
│            NEXT.JS 16 FRONTEND  (frontend-v3 / React 19)        │
│  · /upload   /calls   /hitl   /tracker   /rules   /scripts     │
│  · /observability  → /audit  /failed-jobs  /stuck              │
│  · agent chat (RAG-grounded reviewer assistant)                 │
│  TanStack Query · zustand · shadcn · Playwright · Vitest       │
└──────────────────────┬──────────────────────────────────────────┘
                       │ HTTPS via Cloudflare Tunnel
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                CLOUDFLARE TUNNEL  (cloudflared on amina)        │
│   SSL termination · DDoS at edge · no public ports on VPS      │
│   replaces a separate API Gateway / Nginx                      │
└──────────────────────┬──────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                FASTAPI 0.115  (Python 3.12, app/main.py)        │
│  · save audio → Supabase Storage bucket "call-audio"           │
│  · INSERT Call row → Supabase Postgres (status: queued)        │
│  · record_audit("call.upload") → audit_log (hash chain)        │
│  · emit Inngest event "call/uploaded"                          │
│  · return call_id immediately (non-blocking)                   │
└──────┬──────────────────────────────────────┬───────────────────┘
       │ event-driven                         │ read/write
       ▼                                      ▼
┌──────────────────────┐           ┌──────────────────────────────┐
│   INNGEST            │           │   SUPABASE POSTGRES          │
│   (cloud free tier   │           │   + pgvector + Alembic       │
│    OR self-hosted    │           │                              │
│    Apache 2.0 binary)│           │   calls  call_checkpoints    │
│                      │           │   scripts  customers  deals  │
│   process_call       │           │   rules_chunks  directives   │
│   redispatch_watchdog│           │   rejections  flags          │
│   rag_ingest_*       │           │   audit_log (hash chain)     │
│                      │           │   failed_jobs                │
│   step memoization   │           │   agent_learnings (vector)   │
│   at-least-once      │           │   review_sessions / claims   │
│   retries + backoff  │           │   tracker rows / xlsx import │
└──────────┬───────────┘           └──────────────┬───────────────┘
           │ runs durable function                 │ writes per step
           ▼                                       │
┌─────────────────────────────────────────────────────────────────┐
│            DURABLE PIPELINE (app/workflows/process_call.py)     │
│            6 steps, each wrapped in ctx.step.run(...)           │
│                                                                 │
│  1. DOWNLOAD_AUDIO       (timeout 120s)                        │
│     └── pull from Supabase Storage to /tmp                     │
│                                                                 │
│  2. TRANSCRIBE           (timeout 300s) — multi-engine tribunal │
│     └── asyncio.gather across engines:                         │
│         Deepgram · AssemblyAI · Speechmatics · Groq            │
│         OpenAI Whisper · Cohere · Gemini audio-native          │
│     └── tribunal_wer.py picks consensus + tags field source    │
│                                                                 │
│  3. DETECT_METADATA      (timeout 60s)                         │
│     └── agent name + supplier + script variant detection        │
│     └── business name + customer fuzzy match                    │
│                                                                 │
│  4. ANALYZE_CHECKPOINTS  (timeout 420s) — tiered Smart Agent   │
│     └── Tier 1: Gemini 2.5 Flash (fast/cheap)                  │
│     └── Escalate: Claude Sonnet 4.6 when confidence == low     │
│     └── Tool-using agent (search_rules / loas / supplier_docs) │
│     └── Embedding pre-filter (Wave-4, flag-gated): pgvector    │
│         cosine sim chunk×rule before LLM fan-out               │
│     └── Delete-then-insert CallCheckpoint rows (idempotent)    │
│     └── Vulnerable-customer + pricing-mismatch detectors        │
│                                                                 │
│  5. SCORE                (timeout 60s)                         │
│     └── derive_compliance(call) → score, status, reason        │
│     └── highest-confidence dedupe per checkpoint               │
│     └── flag missing required checkpoints                       │
│                                                                 │
│  6. FINALIZE             (timeout 30s)                         │
│     └── HITL routing (rejection_factory)                       │
│     └── deal lifecycle update (deal_lifecycle / deal_verdict)  │
│     └── commit Call row + emit completion event                 │
└──────────────────────┬──────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                    DURABILITY LAYER                              │
│  Inngest at-least-once delivery     ≈ acks_late=True            │
│  Inngest step memoization           ≈ Redis checkpoints         │
│  Inngest exponential backoff        → built-in retry curve      │
│  redispatch_watchdog cron           → sweep stuck >7min         │
│  _handle_exhausted_run              → flip status=failed +      │
│                                       INSERT failed_jobs row    │
│  Per-step _STEP_TIMEOUTS in code    → soft caps below watchdog  │
│  audit_log tamper-evident hash chain (prev_hash → this_hash)    │
│  Call.last_step_name + last_step_error                          │
│                          → forensic breadcrumb on partial fail  │
└──────────────────────┬──────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                    OBSERVABILITY LAYER                           │
│  GlitchTip (Sentry-API compat, self-host)                       │
│      → catches uncaught exceptions FastAPI + Next.js            │
│  Loguru                                                         │
│      → structured JSON logs tagged by call_id                   │
│  Promtail → Loki                                                │
│      → log aggregation, search by call_id in LogQL              │
│  Prometheus + prometheus-fastapi-instrumentator                 │
│      → scrape metrics every 15s (queue depth, p50/p95/p99,      │
│        rule-failure rate, throughput, error rate per route)    │
│  Grafana (4 seed dashboards: Pipeline · LLM · API · Errors)    │
│  Inngest Dashboard :8288                                        │
│      → run state, retries, step memoization, replay button     │
│      (replaces Flower for our queue model)                     │
│  /api/observability/audit · /failed-jobs · /stuck               │
│      → custom read APIs over forensic tables                    │
└─────────────────────────────────────────────────────────────────┘


2. Layer-by-Layer Breakdown

2.1  API Layer — FastAPI 0.115 (Python 3.12)
The entry point. Receives audio uploads from the Next.js frontend, persists
them to Supabase Storage, creates a Call row in Supabase Postgres, writes an
audit_log row, and emits an Inngest "call/uploaded" event. Returns call_id
immediately.

  Why FastAPI
  Async-native — handles many concurrent requests without blocking
  Automatic OpenAPI at /docs
  Pydantic typing at boundaries
  Best ecosystem for Python AI/LLM backends

  Routers actually mounted (app/main.py)
  routes (uploads, status, replay-stub) · agents · agent_chat · customers
  deals · directives · email · flags · hitl · import_xlsx · observability
  rag · rag_admin · rejections · rules · saved_views · script · tracker
  tracker_edit · plus inngest.fast_api.serve(...)

2.2  Event Bus + Durable Workflow — Inngest (NOT RabbitMQ + Celery)
Inngest replaces the spec's original RabbitMQ + Celery + Redis trio. One
event ("call/uploaded") triggers one durable function (process_call). Inngest
hosts the event bus + scheduler + step state + dashboard. No RabbitMQ, no
AMQP, no broker container on the VPS.

Why Inngest over RabbitMQ + Celery
  • Collapses 3 services (broker + result store + checkpoint cache) into 1
  • At-least-once delivery + step memoization give the same durability
    semantics as acks_late + Redis checkpoints — by default, no manual code
  • Replay button + step graph in the dashboard, free
  • Free cloud tier (≤50k runs/mo) covers v1 demo + first paying customers
  • Apache-2.0 self-hosted single-binary path exists if vendor-coupling
    becomes a concern (Phase-2 SOC2 / enterprise client policy)

Why not RabbitMQ + Celery for v1
  • 3 extra containers to operate (broker + Redis + Flower)
  • Manual checkpoint code in every step
  • No native step graph or replay
  • Spec's two-pool isolation deferred until starvation actually measured —
    today single function fits the volume

When we'd reverse course
  • Phase-2 enterprise / SOC2: clients demand "all infra you own" → Inngest
    self-host or Celery + RabbitMQ + Valkey
  • Volume hits Inngest paid-tier inflection (>50k runs/mo)
  • Customer regulator demands AMQP specifically

2.3  Result Backend + Checkpoints — Inngest step memo + Postgres Call row
There is NO Redis in production. The two roles spec described (task results +
pipeline checkpoints) are met by:
  • Inngest memoizes each ctx.step.run(...) by (function_id, step_name,
    input_hash) — on retry, completed steps are skipped automatically
  • The Call row carries last_step_name + last_step_error + intermediate
    artifacts (transcript, word_data, score, reason)
  • /api/observability/stuck surfaces last_step_started_at vs NOW() so
    reviewers see exactly where a run paused

  Replay example
  POST /api/calls/:id/reanalyze (Wave-3) re-runs steps 4–6 only, reading
  the stored transcript. Zero re-transcription cost.

2.4  Database — Supabase Postgres + Alembic + pgvector
Postgres is managed by Supabase (eu-west-1 pooler). Schema owned by Alembic
(backend/alembic/versions/, ~60+ migrations). Tables go far beyond the
original spec's "3 tables":

  Core compliance:        calls · call_checkpoints · scripts · rules_chunks
  Customer / deal:        customers · customer_deals · directives
  HITL workflow:          review_sessions · claim_locks · profiles
  Quality:                rejections · flags · agent_learnings (vector)
  Tracker:                tracker_rows · xlsx_imports
  Audit / forensics:      audit_log (tamper-evident hash chain) · failed_jobs
  RAG:                    rule_chunks · loa_chunks · supplier_doc_chunks
                          + pgvector indexes on each

  Why audit_log uses a hash chain
  Each row carries prev_hash + this_hash. A tamper attempt that mutates a
  past payload invalidates every subsequent this_hash — a future verifier
  proves row integrity without external WORM storage. Implemented in
  app/audit.py:record_audit().

  The replay feature (unchanged from spec)
  Raw transcript persisted in Postgres → re-run analysis on historical
  calls when rule catalog changes → no re-transcription cost.

2.5  Object Storage — Supabase Storage (NOT raw S3)
Audio files live in Supabase Storage bucket "call-audio". Pipeline downloads
to a temp file in step 1, processes, discards. Frontends play audio via a
short-lived signed URL minted server-side with the Supabase service-role key.

Where this differs from earlier spec drafts
  Earlier spec proposed S3 / Hetzner Object Storage with endpoint swap by
  env var. Code uses the Supabase Python SDK directly. Phase-2 portability
  is closed in Wave-3: introduce StorageBackend ABC with supabase_backend
  + s3_backend impls, selectable by STORAGE_BACKEND env var. boto3-driven
  S3 backend works against AWS S3, MinIO, Cloudflare R2, Hetzner Object
  Storage interchangeably.

2.6  Frontend — Next.js 16 + React 19 (NOT generic React)
Same-origin API proxy via Next rewrites, TanStack Query polling, Zustand for
client state, shadcn/ui + Tailwind, Playwright + Vitest. Hosts the reviewer
console: HITL queue, call detail, agent chat, rule catalog browser, tracker
board, observability dashboards, saved views.


3. Pipeline Deep Dive

3.1  Transcription — multi-engine consensus tribunal
Audio is downloaded from Supabase Storage to /tmp, then sent in parallel to
multiple STT engines:
  Deepgram · AssemblyAI · Speechmatics · Groq · OpenAI Whisper · Cohere ·
  Gemini (audio-native).

backend/app/tribunal_wer.py picks the consensus output and tags each field
with its source (field_sources.py). Benchmark suite (accuracy_benchmark.py,
benchmark_audio_native.py, benchmark_layer2.py) measures WER per engine on
ground-truth calls in backend/benchmark/.

  Why tribunal vs single AssemblyAI
  Compliance accuracy is the moat. Single-engine STT loses 5–10% WER on
  noisy mobile call audio; tribunal recovers most of it. Cost is higher but
  per-call quality is the product.

3.2  Chunking
Speaker turns from the consensus transcript become natural chunk boundaries.
Sliding-window fallback (300 tokens, 50 overlap, tiktoken) is available but
rarely fires when at least one engine returned diarization.

  Embedding pre-filter (Wave-4, flag-gated)
  app/rag/embed.py + pgvector already exist and ingest rule chunks. Wave-4
  wires cosine sim chunk×rule before LLM fan-out, behind
  embedding_prefilter_enabled flag. Cuts LLM cost ~50–80% on typical calls.

3.3  Fan-Out Rule Analysis — tiered Smart Agent (NOT Claude Haiku flat)
For each chunk, relevant rules are checked in parallel via the Smart Agent
loop (app/agent/agent_loop.py, gated by use_agent_analyzer):

  Tier 1   Gemini 2.5 Flash       (cheap, fast first pass)
  Escalate Claude Sonnet 4.6      (when confidence == low)
  Tools    search_rules · search_loas · search_supplier_docs ·
           get_rejection_rules
  Limits   max 8 tool-use turns per batch before forcing a verdict
  Output   Pydantic-validated checkpoint result (rule_id, status, evidence,
           reason)

Per-rule retry uses tenacity with exponential backoff. Failures are logged
to last_step_error, not silenced.

  Why tiered, not Haiku flat (as earlier spec proposed)
  Sonnet 4.6 + Smart Agent escalation hits ~10 percentage points higher
  rule accuracy on the benchmark set. Cost is mitigated by the tiered
  design: 90%+ of rule checks finish at Gemini Flash tier. With Wave-4's
  embedding pre-filter, blended cost approaches Haiku-equivalent.

3.4  Aggregation — derive_compliance + rejection_factory
After all checkpoints land, app/compliance.derive_compliance() computes:
  • per-call score and pass/fail status
  • highest-confidence dedupe per rule
  • missing required-rules list
  • HITL routing decision (which reviewer queue, priority)

build_rejection_for_call (app/rejection_factory) constructs structured
rejection records when score / flags warrant. Deal-level rollup
(deal_verdict.py + deals_composite.py) aggregates verdicts across calls
sharing a customer_deal.


4. Durability Layer

A compliance system cannot lose calls. Five mechanisms work together:

Inngest at-least-once delivery
  └── event stays delivered until the function returns successfully
  └── worker crash mid-step → next attempt re-enters at the same step

Inngest step memoization
  └── each ctx.step.run(...) keyed by (function_id, step_name, input_hash)
  └── on retry, completed steps are skipped — no re-transcription

Per-step _STEP_TIMEOUTS (process_call.py)
  └── download_audio 120s · transcribe 300s · detect_metadata 60s
  └── analyze_checkpoints 420s · score 60s · finalize 30s
  └── soft caps below the 7-minute watchdog threshold

redispatch_watchdog cron
  └── _STUCK_QUERY     picks runs with watchdog_redispatch_count < 1 → redispatch
  └── _EXHAUSTED_QUERY picks runs that already burned their one redispatch and
      are still stuck → calls _handle_exhausted_run

_handle_exhausted_run
  └── reads Call.last_step_name + last_step_error
  └── INSERT into failed_jobs (idempotent on (call_id, attempts) unique index)
  └── flips Call.status = 'failed'
  └── reviewer sees the row in /api/observability/failed-jobs
  └── replay via the (Wave-3) /reanalyze endpoint

audit_log hash chain
  └── every mutation writes a row with this_hash linked to prev_hash
  └── readable at /api/observability/audit
  └── tamper-evident: any retroactive edit invalidates downstream this_hash


5. Observability Layer (Wave-2 deliverables)

Five tools cover all failure modes. All free / OSS — no Sentry SaaS, no
Datadog. Together they answer any question about what is happening now or
what happened in the past.

5.1  GlitchTip — Self-Hosted Sentry-API Compatible Error Tracking
GlitchTip is the OSS Sentry-API-compatible alternative. Runs as a Docker
service on amina (Wave-2). The official sentry-sdk[fastapi] + @sentry/nextjs
SDKs both point at the GlitchTip URL (set via SENTRY_DSN env var). Captures
unhandled exceptions with call_id + step + request context.

  Priority: ship Wave-2 first
  Free + open source (MIT)
  Same SDK as sentry.io — zero code rewrite if we ever swap
  Docker compose service: glitchtip + Postgres-backed (or reuse Supabase)
  Alerts via email / webhook when new errors appear

5.2  Loguru — Structured Logging
Every log line is bound with call_id + step and written as JSON. Single
call traceable end-to-end across FastAPI and Inngest by filtering on call_id.

  app_log.bind(call_id=call_id, step="transcribe").info(
      "WORKFLOW_STEP status=ok duration_ms=4820"
  )

  In Grafana Loki:
  {compose_service="compliance-backend"} |= "call_id=abc-123"
  → see every step the call took

5.3  Loki + Promtail — Log Aggregation
Promtail tails Docker stdout (single config, all containers) and ships JSON
log lines to Loki. Loki indexes by labels (compose_service, level, call_id)
and stores log bodies efficiently. LogQL queries from Grafana.

  Why Loki over Datadog Logs
  Loki is free OSS (Apache 2.0), self-hosted alongside Grafana on the
  same VPS, integrates natively with Grafana. Datadog is excellent but
  $$$ at any volume.

5.4  Prometheus + prometheus-fastapi-instrumentator — Metrics
Prometheus scrapes /metrics on FastAPI every 15s. The instrumentator
exposes per-route latency histograms, RPS, request size, and error counters
out of the box. Custom counters live in app/observability_metrics.py
(Wave-2): pipeline_step_duration_seconds, llm_call_total,
llm_call_duration_seconds, rule_failure_total.

Key metrics to monitor:
  • Inngest run rate + p95 latency per function
  • LLM call latency p50 / p95 / p99 per tier (Flash vs Sonnet)
  • Rule check failure rate per rule
  • Per-step pipeline duration (download / transcribe / analyze / score)
  • API error rate per route
  • Supabase connection pool saturation

5.5  Grafana — Dashboards on Loki + Prometheus
Single Docker service running both LogQL (Loki) + PromQL (Prometheus).
Wave-2 ships 4 seed dashboards as JSON in
infrastructure/grafana/dashboards/:
  • Pipeline   per-step duration p50/p95/p99, throughput
  • LLM        call rate, escalation rate, token cost
  • API        RPS, p50/p95/p99, error rate per route
  • Errors     top exception types, last seen, frequency

5.6  Inngest Dashboard — Workflow Run Monitor (replaces Flower)
At :8288 (dev) or the cloud dashboard. Shows every run in real time —
function name, run_id, step graph, input/output per step, retry count,
which Inngest worker handled it. Replay button re-executes from any past
step. Free with Inngest.

  Why Inngest Dashboard, not Flower
  No Celery → no Flower. Inngest's dashboard is richer than Flower
  because it captures step inputs and outputs by design (Flower only
  surfaces task names + args + results).

5.7  Custom /api/observability Read APIs (Wave-1, shipped)
Three FastAPI endpoints for reviewer-facing forensics:
  GET /api/observability/audit         → recent audit_log rows w/ hash chain
  GET /api/observability/failed-jobs   → recent Inngest exhaustion failures
  GET /api/observability/stuck         → in-flight runs + last_step forensics

5.8  Debug Workflow
When a call fails, investigation order:

1. GlitchTip alert  → exception type + stack trace + line number
                     → understand what kind of failure it is

2. Grafana Loki     → search logs by call_id
                     → see every step the call took
                     → find exactly where it stopped

3. Prometheus       → check if failure is isolated or systemic
                     → all LLM calls failing = provider 429 or key issue
                     → one call failing = data or logic issue

4. Inngest Dashboard → confirm run state, check retry count
                     → see which step memoized vs re-ran

5. /observability   → reviewer surfaces failed_jobs + audit chain


6. Tools Summary & Alternatives

Layer                       Chosen                              Free OSS?         Alternative
─────────────────────────────────────────────────────────────────────────────────────────────────
API Framework               FastAPI 0.115                      ✅ BSD             Litestar
Frontend Framework          Next.js 16 + React 19              ✅ MIT             Remix
Edge / TLS                  Cloudflare Tunnel (cloudflared)    ✅                 Caddy + Let's Encrypt
Event Bus + Workflow        Inngest cloud free tier            ✅ Apache 2.0      Inngest self-host (same code) ·
                                                                                  Temporal · Hatchet ·
                                                                                  Celery+RabbitMQ+Valkey
Database                    Supabase Postgres (managed)        ✅ Apache 2.0      Self-host Postgres + pgvector
ORM / Migrations            SQLAlchemy 2.0 + Alembic           ✅ MIT             —
Vector Search               pgvector + text-embedding-3-small  ✅ MIT             —
Object Storage              Supabase Storage SDK               ✅                 MinIO · Cloudflare R2 · AWS S3
                            (Wave-3 abstracts behind ABC)                         (one env var swap after Wave-3)
Audio Transcription         Tribunal: Deepgram + AssemblyAI +  ✅ (engines        Self-hosted Whisper
                            Speechmatics + Groq + OpenAI       paid per call)    (adds infra complexity)
                            + Cohere + Gemini
LLM (rule analysis)         Tier 1: Gemini 2.5 Flash           ✅ (paid per call) Claude Haiku flat (cheaper,
                            Escalate: Claude Sonnet 4.6                          but ~10pp lower accuracy)
LLM Gateway                 OpenRouter / Anthropic /           ✅                 Direct provider SDKs
                            OpenAI / Gemini direct                               (less failover)
Smart Agent                 app/agent/agent_loop.py            ✅ (our code)      —
                            (tool-using, max 8 turns)                            
RAG                         pgvector indexes on rule_chunks    ✅                 Pinecone · Weaviate
                            loa_chunks · supplier_doc_chunks                     (paid SaaS)
HITL Console                /hitl reviewer queue + claim/      ✅ (our code)      —
                            release locks + audit
Compliance Replay           POST /calls/:id/reanalyze (Wave-3) ✅ (our code)      —
Audit Log                   audit_log (tamper-evident hash chain) ✅ (our code)   —
Failed-Job Forensics        failed_jobs + watchdog (Wave-1)    ✅ (our code)      —
Error Tracking              GlitchTip self-host (Wave-2)       ✅ MIT             Sentry SaaS · Sentry self-host
Log Aggregation             Loki + Promtail (Wave-2)           ✅ Apache 2.0      Datadog Logs (paid)
Metrics                     Prometheus + Grafana (Wave-2)      ✅ Apache 2.0      Datadog (paid)
Workflow Monitor            Inngest Dashboard                  ✅                 Flower (Celery only — n/a)
Reverse Proxy / TLS         Cloudflare Tunnel                  ✅                 Nginx + certbot · Caddy
Container Orchestration     Docker Compose                     ✅ Apache 2.0      —
IaC                         OpenTofu (Cloudflare DNS only)     ✅ MPL 2.0         Terraform
Infra Provider              Contabo VPS (161.97.178.185)       —                  Hetzner · DigitalOcean · OVH
CI                          GitHub Actions (Wave-1):           ✅ free private    GitLab CI · Drone · Forgejo
                            test.yml + coverage.yml +          2k min/mo
                            touched-fns-gate.yml
Backups                     Supabase managed +                 ✅                 Self-hosted pg_dump cron
                            pg_dump nightly cron (Wave-3)                        + restic to S3-compatible
Test Discipline             pytest + vitest + Playwright       ✅                 —
                            (CI gated by required checks,
                            Wave-1)


7. Deployment Strategy

Phase 1 — Contabo VPS (Ship Now)
  Recommended for: POC → first paying customers
  Cost: ~$10–25/month VPS + ~$25/mo Supabase Pro (when PITR needed)
  Full control — SSH in, inspect anything
  No cloud account setup required
  No Nginx · No RabbitMQ · No Redis · No Celery · No Flower

Contabo VPS  (~$10–25/month, 161.97.178.185)
  └── Docker Compose manages all services on one host
  └── Cloudflare Tunnel for SSL + ingress (no public ports on VPS)
  └── All services on one server:
        compliance-backend  :8001  (FastAPI)
        compliance-frontend :9000  (Next.js standalone)
        cloudflared                (Cloudflare Tunnel client)
        glitchtip          :8080  (Wave-2 — error tracking)
        prometheus         :9090  (Wave-2 — metrics)
        grafana            :3001  (Wave-2 — dashboards)
        loki               :3100  (Wave-2 — log store)
        promtail                  (Wave-2 — Docker socket reader)
        pg_dump_cron               (Wave-3 — alpine + cron sidecar)
  └── Postgres + Storage    Supabase managed (eu-west-1 pooler)
  └── Inngest               Cloud free tier OR self-hosted single binary

OpenTofu (infrastructure/contabo/) provisions the Cloudflare DNS record only.
Contabo's Terraform provider too thin for full VM IaC; VPS lifecycle is
documented as an SSH + Docker Compose runbook in
infrastructure/contabo/README.md.

GitHub Actions (Wave-5) → SSH amina → docker compose pull && up -d.

Phase 2 — AWS / Azure (When Clients Demand It)
  Recommended when: enterprise clients, SOC2 / ISO27001, multi-region
  Cost: ~$200–250/month managed services
  Same app code — only environment variables change

Phase 1 (Contabo + Supabase + Inngest cloud)
Phase 2 (AWS — example)
Supabase Postgres
RDS PostgreSQL (Multi-AZ, auto-backups)
Inngest cloud
Inngest self-host (Apache 2.0 binary on ECS/EKS) OR Temporal Cloud
Supabase Storage SDK
S3 (StorageBackend ABC swap to s3_backend)
Docker Compose on Contabo
ECS Fargate (auto-scaling containers)
Cloudflare Tunnel
ALB + ACM certificate
SSH + manual deploy
Terraform/OpenTofu + GitHub Actions CI/CD
GlitchTip self-host
GlitchTip self-host (containerized) OR Sentry SaaS
Loki + Prometheus + Grafana
Same on EKS, OR CloudWatch + Managed Grafana

  Migration is mostly env var changes, not code rewrites
  DATABASE_URL=postgresql+asyncpg://...  → just change the host
  STORAGE_BACKEND=s3                     → flip flag (Wave-3 ABC)
  S3_ENDPOINT=...                        → set per provider
  INNGEST_BASE_URL=...                   → point at self-hosted instance
  SENTRY_DSN=...                         → still GlitchTip URL


8. Infrastructure as Code — OpenTofu (NOT Terraform fork)

OpenTofu is the OSS-governed Terraform fork (MPL 2.0, Linux Foundation hosted)
that emerged after HashiCorp's BSL re-license. We use it because it's the
free-OSS path forward. CLI is drop-in compatible with Terraform 1.5+.

Today we manage one resource: the Cloudflare A record pointing
compliance.<domain> at the Contabo VPS IPv4. Everything else (the VPS itself,
firewall rules, Docker Compose state) is documented as an SSH+Compose runbook
because Contabo's Terraform provider is too thin for full VM IaC.

infrastructure/
└── contabo/
    ├── versions.tf       OpenTofu + Cloudflare provider versions
    ├── variables.tf      cloudflare_*, vps_ipv4, subdomain
    ├── dns.tf            cloudflare_record.compliance_apex (A → vps_ipv4)
    ├── README.md         Contabo SSH + Docker Compose runbook
    ├── .gitignore        ignore tfstate, tfvars
    └── (no main.tf — Contabo provider too thin to manage VM lifecycle)

Phase-2 will add (when migration committed):
  infrastructure/aws/      ECS + RDS + S3 + ALB
  infrastructure/azure/    App Service + PostgreSQL + Service Bus

Why OpenTofu over Terraform
  Same language (HCL), same providers (Hetzner, Cloudflare, AWS, Azure, GCP),
  but truly OSS-governed. CLI: `tofu init`, `tofu plan`, `tofu apply`.
  Drop-in compatible.


9. Environment Variable Abstraction (Pydantic Settings)

backend/app/config.py uses pydantic-settings with .env file loading. Every
infrastructure URL / API key / feature flag is an env var.

  # .env — Phase 1 (Contabo + Supabase + Inngest cloud)
  DATABASE_URL=postgresql+psycopg2://...@aws-0-eu-west-1.pooler.supabase.com:6543/postgres
  SUPABASE_URL=...
  SUPABASE_SERVICE_ROLE_KEY=...
  SUPABASE_STORAGE_BUCKET=call-audio
  ANTHROPIC_API_KEY=...
  OPENROUTER_API_KEY=...
  GEMINI_API_KEY=...
  OPENAI_API_KEY=...
  DEEPGRAM_API_KEY=...
  ASSEMBLYAI_API_KEY=...
  SPEECHMATICS_API_KEY=...
  USE_INNGEST_PIPELINE=true
  USE_AGENT_ANALYZER=true
  AGENT_ESCALATION_THRESHOLD=low
  EMBEDDING_PREFILTER_ENABLED=false       # flips True after Wave-4 A/B passes
  PRICING_MISMATCH_ENABLED=true
  VULNERABLE_DETECTION_ENABLED=true
  STORAGE_BACKEND=supabase                # Wave-3 — supabase | s3
  SENTRY_DSN=https://...@glitchtip.amina.../1   # Wave-2

  # .env — Phase 2 (AWS — same app, different values)
  DATABASE_URL=postgresql+asyncpg://...@rds-endpoint:5432/compliance
  STORAGE_BACKEND=s3
  S3_ENDPOINT=                            # empty = AWS default
  S3_BUCKET=compliance-audio-prod
  S3_ACCESS_KEY=...
  S3_SECRET_KEY=...
  INNGEST_BASE_URL=http://inngest-self-host.internal:8288
  SENTRY_DSN=https://...@glitchtip.internal/1

  Rule
  If it points to infrastructure, it is an env var. If it tunes runtime
  behavior (LLM model, escalation threshold, feature flag), it is also
  an env var (config.py exposes them as Pydantic fields with defaults).


10. Cost Comparison

Service                         Phase 1 (Contabo today)         Phase 2 (AWS — example)
─────────────────────────────────────────────────────────────────────────────────────
Compute (API + Frontend)        Contabo VPS:    ~$10–25/mo      ECS Fargate 3 services: ~$80–120/mo
Database                        Supabase Free → Pro $25         RDS PostgreSQL small:   ~$50/mo
                                (PITR + 7-day backups when Pro)
Object Storage                  Supabase included               S3:                     ~$5–20/mo
Event Bus / Workflow            Inngest free tier (≤50k runs)   Inngest self-host:      ~$15/mo
                                                                OR Temporal Cloud:      ~$200/mo
Reverse Proxy / TLS             Cloudflare Tunnel: $0           ALB + ACM:              ~$20/mo
Error Tracking                  GlitchTip self-host: $0         GlitchTip self-host: $0
                                (RAM cost absorbed)
Log Aggregation                 Loki + Promtail: $0             Same OR CloudWatch:     ~$30/mo
                                (RAM cost absorbed)
Metrics                         Prometheus + Grafana: $0        Same OR Managed Grafana: ~$20/mo
                                (RAM cost absorbed)
CI                              GitHub Actions free tier        Same
─────────────────────────────────────────────────────────────────────────────────────
Fixed monthly floor             ~$10–50                         ~$200–250
Per-call variable               STT tribunal + LLM + embeddings dominate (both phases)

  Recommendation
  Start on Contabo + Supabase + Inngest cloud — ship fast, prove the
  product, collect revenue.
  Migrate to AWS / Azure only when clients require enterprise SLAs or
  certifications.
  StorageBackend ABC + env-var abstraction + OpenTofu + Inngest self-host
  path make this migration a few days of work, not a rewrite.


11. Wave Roadmap

Wave 1 — Foundation (SHIPPED, branch feat/wave1-foundation)
  · CI required-checks (test.yml + coverage.yml + touched-fns-gate.yml)
  · PR template
  · failed_jobs migration + ORM + record_failed_job() writer
  · _handle_exhausted_run() in redispatch_watchdog
  · /api/observability/{audit,failed-jobs} read APIs
  · record_audit() coverage across mutating routers
  · OpenTofu scaffolding (Cloudflare DNS only)
  · Contabo SSH+Compose runbook
  · AuditLog ORM model (cross-platform SQLite + Postgres)

Wave 2 — Observability (next)
  · GlitchTip self-host + sentry-sdk wiring (FastAPI + Next.js)
  · Loki + Promtail + Prometheus + Grafana single Compose overlay
  · prometheus-fastapi-instrumentator
  · 4 seed dashboards (Pipeline · LLM · API · Errors)

Wave 3 — Durability + Portability
  · StorageBackend ABC (Supabase + S3 impls, env-var swap)
  · POST /api/calls/:id/reanalyze (replay endpoint)
  · pg_dump nightly cron sidecar + Supabase Storage backups/ bucket
  · First documented restore drill

Wave 4 — Cost (flag-gated, A/B-guarded)
  · Embedding pre-filter (pgvector cosine sim chunk×rule)
  · Tiered LLM default flip (use_agent_analyzer=True)
  · 50-call A/B parity sample → ≥98% verdict parity → flip prod flag

Wave 5 — Deploy + Protection
  · GitHub Actions deploy.yml (SSH amina, compose pull && up -d)
  · Branch protection on main (required checks + linear history + no force-push)
  · Documentation pass: docs/durability.md, docs/runbook.md


12. Spec Heritage — What v0 Got Right (Honor Forever)

The v0 of this document picked specific tools we did not keep (RabbitMQ,
Celery, Redis-as-broker, Sentry SaaS, Hetzner, single-engine AssemblyAI,
Claude Haiku flat). Those got swapped for valid reasons. But the v0
ARCHITECTURAL PRINCIPLES were correct and remain the contract every wave
above must satisfy:

12.1  Env-var abstraction (Pydantic Settings) — ✅ DONE
   Every infrastructure URL + API key + feature flag is an env var. No
   hardcoded endpoints. Phase-1 → Phase-2 migration is mostly env-var
   diffs, not code rewrites. backend/app/config.py enforces this via
   pydantic-settings.

12.2  Durability via at-least-once delivery + checkpoints — ✅ DONE
   Spec said acks_late=True + Redis checkpoints + exponential backoff.
   We kept the SEMANTICS (jobs cannot be lost; retries skip completed
   work) and changed the IMPLEMENTATION to Inngest (at-least-once
   delivery + step memoization + built-in backoff + redispatch_watchdog
   + _handle_exhausted_run + failed_jobs forensics).

12.3  Tamper-evident audit_log — ✅ DONE (and EXTENDED)
   Spec called for an audit_log table; we extended to a tamper-evident
   hash chain (prev_hash → this_hash via sha256). Migration
   497bd38e5551 + app/audit.py:record_audit() + Wave-1 expansion across
   mutating routes (/upload, hitl claim/release, script CRUD, deal create).

12.4  Replay feature on stored transcript — ⏳ WAVE 3
   Spec's killer feature: when rules change, re-run on historical calls
   without re-transcription. Wave-3 ships POST /api/calls/:id/reanalyze
   that re-runs steps 4 (analyze) → 5 (score) → 6 (finalize) only,
   reading the persisted transcript. Zero re-transcription cost.

12.5  Loguru → log aggregation — ⏳ WAVE 2
   Loguru already in production with structured JSON tagged by
   call_id + step. Wave-2 ships Promtail → Loki + Grafana so a single
   call is queryable end-to-end via {compose_service="..."} |= "call_id=X".

12.6  Sentry-shape error tracking — ⏳ WAVE 2 (via GlitchTip)
   Spec recommended Sentry "first 30 minutes." We kept the SDK
   (sentry-sdk[fastapi] + @sentry/nextjs) and swapped the destination to
   self-hosted GlitchTip (MIT, Sentry-API compatible). Free, OSS, same
   SDK code as sentry.io — zero rewrite if we ever swap back.

12.7  Prometheus + Grafana metrics — ⏳ WAVE 2
   prometheus-fastapi-instrumentator on /metrics + custom counters in
   app/observability_metrics.py + 4 seed dashboards (Pipeline · LLM ·
   API · Errors). All free/OSS, all on the same VPS.

12.8  Infrastructure as Code — ✅ WAVE 1 (partial), ⏳ WAVES 2-5 (extended)
   OpenTofu (OSS Terraform fork, MPL 2.0) manages Cloudflare DNS today.
   Contabo provider too thin for full VM IaC; the VPS itself is
   documented as an SSH+Docker-Compose runbook. Phase-2 will add
   infrastructure/aws/ and infrastructure/azure/ as additive directories.

12.9  LLM cost discipline via embedding pre-filter — ⏳ WAVE 4
   Spec's "upgrade path": replace keyword-based rule relevance with
   embedding similarity, route only relevant chunks to the LLM. We
   already have pgvector + text-embedding-3-small + ingested rule_chunks
   from RAG. Wave-4 wires cosine sim chunk×rule before LLM fan-out
   (flag embedding_prefilter_enabled, default False until A/B parity
   proven on a 50-call sample). Cuts LLM cost ~50–80% on typical calls.
   Combined with the tiered LLM (Gemini Flash → Sonnet escalate),
   blended per-call cost approaches Haiku-equivalent without the
   accuracy loss.

   Status check across the 9 principles:
     5 / 9   delivered (env-vars, durability, audit chain, OpenTofu IaC,
                       PR template / CI gates already enforce parts of #1)
     4 / 9   scheduled (replay W3, Loki W2, GlitchTip W2, Prom+Grafana W2,
                       embedding pre-filter W4)

   Anything that drops a principle from this list during execution
   requires an explicit decision recorded in the wave's plan doc, not
   a silent omission.


End of Document
