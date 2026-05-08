COMPLIANCE CALL ANALYSIS SYSTEM — CURRENT ARCHITECTURE
v1 as deployed (May 2026), with Wave-1 hardening landed on feat/wave1-foundation
Stack actually running on amina (Contabo VPS) · Phase 1 → Phase 2 portability deferred

1. System Overview
The system ingests recorded sales / compliance audio calls, transcribes them via a
multi-engine consensus tribunal, chunks the transcript into speaker-turn segments,
runs each chunk against a catalog of compliance rules using a tiered LLM (Gemini
Flash first → Claude Sonnet 4.6 escalate), then routes the verdict through a
Human-In-The-Loop (HITL) reviewer console for final scoring and deal lifecycle
tracking. The output is a structured compliance verdict per call plus aggregate
deal-level rollups, surfaced to reviewers via the Next.js console and persisted
in Supabase Postgres for replay and audit.

┌─────────────────────────────────────────────────────────────────┐
│            NEXT.JS 16 FRONTEND  (frontend-v3 / React 19)        │
│  · /upload         → audio upload + same-deal toggle           │
│  · /calls          → list + detail + transcript viewer         │
│  · /hitl           → reviewer claim/release queue              │
│  · /tracker        → XLSX import/export tracker board          │
│  · /rules /scripts /customers /directives /rejections          │
│  · /observability  → /audit + /failed-jobs + /stuck            │
│  · agent chat      → RAG-grounded reviewer assistant           │
│  TanStack Query · zustand · shadcn · Playwright · Vitest       │
└────────────────────────────┬────────────────────────────────────┘
                             │ HTTPS via Cloudflare Tunnel
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                CLOUDFLARE TUNNEL  (cloudflared on amina)        │
│   SSL termination · DDoS at edge · no public ports on VPS      │
│   no separate API Gateway service — tunnel + FastAPI direct    │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                FASTAPI 0.115  (Python 3.12, app/main.py)        │
│  · save audio → Supabase Storage bucket "call-audio"           │
│  · INSERT Call row → Supabase Postgres (status: queued)        │
│  · record_audit("call.upload") → audit_log (hash chain)        │
│  · emit Inngest event "call/uploaded" if flag enabled          │
│  · return call_id immediately (non-blocking)                   │
│  · ~25 routers: routes hitl agents_chat customers deals        │
│    directives email flags hitl import_xlsx observability       │
│    rag rag_admin rejections rules saved_views script           │
│    tracker tracker_edit + Inngest webhook mount                │
└──────┬──────────────────────────────────────┬───────────────────┘
       │ enqueue (event-driven)               │ read/write
       ▼                                      ▼
┌──────────────────┐               ┌──────────────────────────────┐
│  INNGEST CLOUD   │               │   SUPABASE POSTGRES          │
│  (free tier)     │               │   + pgvector + alembic       │
│                  │               │                              │
│  process_call    │               │   calls  call_checkpoints    │
│  redispatch_     │               │   scripts  customers  deals  │
│   watchdog cron  │               │   rules_*  directives        │
│  rag_ingest_*    │               │   rejections  flags          │
│                  │               │   audit_log  failed_jobs ★   │
│  step memoization│               │   agent_learnings (vector)   │
│  at-least-once   │               │   review_sessions/claims     │
│  retries+backoff │               │   tracker rows + xlsx import │
└────────┬─────────┘               └──────────────┬───────────────┘
         │ runs durable function                  │ writes per step
         ▼                                        │
┌─────────────────────────────────────────────────────────────────┐
│            DURABLE PIPELINE (app/workflows/process_call.py)     │
│            6 steps, each wrapped in ctx.step.run(...)           │
│                                                                 │
│  1. DOWNLOAD_AUDIO                                              │
│     └── pull from Supabase Storage to temp file                │
│     └── timeout 120s                                            │
│                                                                 │
│  2. TRANSCRIBE — multi-engine tribunal                         │
│     └── asyncio.gather across 5+ engines:                      │
│         Deepgram · AssemblyAI · Speechmatics · Groq            │
│         OpenAI Whisper · Cohere · Gemini audio-native          │
│     └── tribunal_wer.py picks consensus, tags field source     │
│     └── speaker diarization → natural chunk boundaries          │
│     └── timeout 300s                                            │
│                                                                 │
│  3. DETECT_METADATA                                             │
│     └── agent name + supplier + script variant detect          │
│     └── business name + customer fuzzy match                    │
│     └── filename rename                                         │
│     └── timeout 60s                                             │
│                                                                 │
│  4. ANALYZE_CHECKPOINTS — tiered LLM rule analysis             │
│     └── Smart Agent (app/agent/agent_loop.py):                 │
│         Gemini 2.5 Flash first pass (cheap, fast)              │
│         Claude Sonnet 4.6 escalate when confidence == low      │
│     └── tool-using agent (RAG search rules/LOAs/supplier docs) │
│     └── max 8 turns per batch before forcing verdict           │
│     └── delete-then-insert CallCheckpoint rows (idempotent)    │
│     └── extraction layer: vulnerable customer + pricing flags  │
│     └── timeout 420s                                            │
│                                                                 │
│  5. SCORE                                                       │
│     └── derive_compliance(call) → score, status, reason        │
│     └── highest-confidence dedupe per checkpoint               │
│     └── flag missing required checkpoints                       │
│     └── timeout 60s                                             │
│                                                                 │
│  6. FINALIZE                                                    │
│     └── derive HITL routing (rejection_factory)                │
│     └── deal lifecycle update (deal_lifecycle / deal_verdict)  │
│     └── commit Call row + emit completion event                 │
│     └── timeout 30s                                             │
└────────────────────────────┬────────────────────────────────────┘
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
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    OBSERVABILITY LAYER                           │
│  CURRENT (in code):                                              │
│   Loguru        → structured JSON logs tagged by call_id        │
│                   (binds: call_id, step, attempt)               │
│   /api/observability/audit      → audit_log read API ★          │
│   /api/observability/failed-jobs → failed_jobs read API ★       │
│   /api/observability/stuck      → in-flight runs forensics      │
│   Inngest dashboard :8288       → run state, retries, replay   │
│                                                                  │
│  WAVE-2 PLANNED (not yet shipped):                              │
│   GlitchTip self-host           → Sentry-API exception capture │
│   Loki + Promtail               → log aggregation off Docker   │
│   Prometheus + instrumentator   → /metrics scrape endpoint     │
│   Grafana                       → 4 seed dashboards            │
│   Sentry SDK (FastAPI + Next)   → wired at GlitchTip URL       │
└─────────────────────────────────────────────────────────────────┘

★ = shipped on Wave-1 branch feat/wave1-foundation (May 2026)


2. Layer-by-Layer Breakdown

2.1  API Layer — FastAPI 0.115 (Python 3.12)
The entry point. Receives audio uploads from the Next.js frontend, persists them
to Supabase Storage, creates a Call row in Supabase Postgres, writes an audit_log
row, and emits an Inngest "call/uploaded" event. Returns call_id immediately.

  Why FastAPI (unchanged from spec)
  Async-native, automatic OpenAPI at /docs, Pydantic typing at boundaries,
  best ecosystem for Python AI/LLM backends.

  Routers actually mounted (app/main.py)
  routes (uploads, status, replay-stub) · agents · agent_chat · customers
  deals · directives · email · flags · hitl · import_xlsx · observability
  rag · rag_admin · rejections · rules · saved_views · script · tracker
  tracker_edit · plus inngest.fast_api.serve(...)

2.2  Message Broker — Inngest (NOT RabbitMQ)
The spec doc proposed RabbitMQ + Celery. The codebase uses Inngest instead. One
event ("call/uploaded") triggers one durable function (process_call). Inngest
hosts the event bus + scheduler + dashboard. No RabbitMQ, no AMQP, no broker
container on the VPS.

  Why Inngest over RabbitMQ + Celery
  Collapses 3 services (broker + result store + checkpoint cache) into 1
  managed service. At-least-once delivery + step memoization give the same
  durability semantics as acks_late + Redis checkpoints. Free tier covers
  current call volume.

  Trade-off
  Vendor coupling. If Phase 2 (AWS) demands self-hosted only, Inngest must
  either be self-hosted (open source) or replaced with Celery + Amazon MQ.

2.3  Task Queue — Inngest functions (NOT Celery workers)
Three durable functions registered:
  • process_call          — the 6-step pipeline (single function, single concurrency)
  • redispatch_watchdog   — cron sweep of stuck runs >7min
  • rag_ingest_call_fn / rag_ingest_script_fn — async embedding ingest

Spec doc proposed two separate Celery worker pools (worker_pipeline concurrency 2,
worker_analysis concurrency 8) for isolation. The current code has ONE function
covering both transcription and analysis. Two-pool isolation is deferred; will
be added if real-world starvation is measured (currently not).

2.4  Result Backend + Checkpoints — Inngest step memoization + Postgres Call row
There is NO Redis in the production stack. The two roles spec described
(task results + pipeline checkpoints) are met by:

  • Inngest memoizes each ctx.step.run(...) by (function_id, step_name, input_hash)
    → on retry, completed steps are skipped automatically
  • The Call row carries last_step_name + last_step_error + intermediate
    artifacts (transcript, word_data, score, reason)
  • /api/observability/stuck surfaces last_step_started_at vs NOW() so
    reviewers can see exactly where a run paused

  Replay example (real, not aspirational)
  POST /api/calls/:id/reanalyze (Wave-3 endpoint, not yet shipped) re-runs
  steps 4–6 only, reading the stored transcript. Zero re-transcription cost.

2.5  Database — Supabase Postgres + Alembic + pgvector
Postgres is managed by Supabase. Schema is owned by Alembic
(backend/alembic/versions/, ~60+ migrations). Row count is well past the
spec's "3 tables":

  Core compliance:        calls · call_checkpoints · scripts · rules_chunks
  Customer / deal:        customers · customer_deals · directives
  HITL workflow:          review_sessions · claim_locks · profiles
  Quality:                rejections · flags · agent_learnings (vector)
  Tracker:                tracker_rows · xlsx_imports
  Audit / forensics:      audit_log (★ tamper-evident hash chain)
                          failed_jobs (★ Wave-1 new)
  RAG:                    rule_chunks · loa_chunks · supplier_doc_chunks
                          + pgvector indexes on each

  Why audit_log uses a hash chain
  Each row carries prev_hash + this_hash. A tamper attempt that mutates a
  past payload invalidates every subsequent this_hash, so a future verifier
  can prove row integrity without external WORM storage. Implemented in
  app/audit.py:record_audit().

2.6  Object Storage — Supabase Storage (NOT S3)
Audio files live in Supabase Storage bucket "call-audio". The pipeline downloads
to a temp file in step 1, processes, and discards. Frontends play audio via a
short-lived signed URL minted server-side with the Supabase service-role key.

  Where this differs from spec
  Spec proposed S3 / Hetzner Object Storage with endpoint swap by env var.
  Code uses the Supabase Python SDK directly. Phase-2 portability is deferred
  to Wave-3 (StorageBackend ABC will introduce supabase_backend.py + s3_backend.py
  selectable by STORAGE_BACKEND env var).

2.7  Frontend — Next.js 16 + React 19 (NOT generic React)
Far beyond spec scope. Same-origin API proxy via Next rewrites, TanStack Query
for polling, Zustand for client state, shadcn/ui + Tailwind for components,
Playwright + Vitest for tests. Hosts the reviewer console (HITL queue, call
detail, agent chat, rule catalog browser, tracker board, observability dashboards,
saved views).


3. Pipeline Deep Dive

3.1  Transcription — multi-engine tribunal
Audio is downloaded from Supabase Storage to /tmp, then sent in parallel to:
  Deepgram (deepgram-sdk 3.7) · AssemblyAI · Speechmatics · Groq · OpenAI Whisper
  Cohere · Gemini (audio-native)

backend/app/tribunal_wer.py picks the consensus output and tags each field with
its source (field_sources.py). The benchmark suite (accuracy_benchmark.py,
benchmark_audio_native.py, benchmark_layer2.py, accuracy_report.csv) measures
WER per engine on ground-truth calls in backend/benchmark/.

  Why tribunal vs single AssemblyAI
  Compliance accuracy is the moat. Single-engine STT loses ~5–10% WER on
  noisy mobile call audio; tribunal recovers most of it. Cost is higher but
  per-call quality is the product.

3.2  Chunking
Speaker turns from the consensus transcript become natural chunk boundaries.
Sliding-window fallback (300 tokens, 50 overlap, tiktoken) is available but
rarely fires when at least one engine returned diarization.

  Embedding pre-filter (Wave-4, flag-gated)
  app/rag/embed.py + pgvector already exist and ingest rule chunks. Wave-4
  wires cosine sim chunk×rule before LLM fan-out, behind
  embedding_prefilter_enabled flag. Cuts LLM cost ~50–80%.

3.3  Fan-Out Rule Analysis — tiered Smart Agent (NOT Claude Haiku flat)
For each chunk, relevant rules are checked in parallel via the Smart Agent
loop (app/agent/agent_loop.py, gated by use_agent_analyzer):

  Tier 1   Gemini 2.5 Flash         (cheap, fast first pass)
  Escalate Claude Sonnet 4.6        (when confidence == low)
  Tools    search_rules · search_loas · search_supplier_docs · get_rejection_rules
  Limits   max 8 tool-use turns per batch before forcing a verdict
  Output   Pydantic-validated checkpoint result (rule_id, status, evidence, reason)

Per-rule retry uses tenacity with exponential backoff. Failures are logged to
last_step_error, not silenced.

  Why not Claude Haiku flat (as spec proposed)
  Sonnet 4.6 + Smart Agent escalation hits ~10 percentage points higher
  rule accuracy on the benchmark set. Cost is mitigated by the tiered design:
  90%+ of rule checks finish at Gemini Flash tier. With Wave-4's embedding
  pre-filter, blended cost approaches Haiku-equivalent.

3.4  Aggregation — derive_compliance + rejection_factory
After all checkpoints land, app/compliance.derive_compliance() computes:
  • per-call score and pass/fail status
  • highest-confidence dedupe per rule
  • missing required-rules list
  • HITL routing decision (which reviewer queue, priority)

build_rejection_for_call (app/rejection_factory) constructs structured rejection
records when score / flags warrant. Deal-level rollup (deal_verdict.py +
deals_composite.py) aggregates verdicts across calls sharing a customer_deal.


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
  └── selects calls with last_step_started_at < NOW() - 7min, status not in
      ('completed','failed'), watchdog_redispatch_count < 1
  └── re-emits the Inngest event once
  └── if still stuck after that → _handle_exhausted_run flips status=failed
      and writes a failed_jobs row (★ Wave-1)

audit_log hash chain
  └── every mutation writes a row with this_hash = sha256(prev_hash | action |
      entity_type | entity_id | canonical(payload))
  └── any retroactive edit is detectable downstream


5. Observability Layer (current state + Wave-2 plan)

5.1  Loguru — Structured Logging  (CURRENT)
Every log line bound with call_id + step. JSON to stdout, captured by Docker
log driver. No external aggregator yet.

  app_log.bind(call_id=call_id, step=step).info("WORKFLOW_STEP status=ok ...")

5.2  /api/observability — Custom Read APIs  (CURRENT, ★ Wave-1)
  GET /api/observability/audit         → recent audit_log rows w/ hash chain
  GET /api/observability/failed-jobs   → recent Inngest exhaustion failures
  GET /api/observability/stuck         → in-flight runs + last_step forensics

5.3  Inngest Dashboard  (CURRENT — replaces Flower)
  http://localhost:8288 (dev) — run state, retries, step memoization,
  replay button, manual function invocation.

5.4  GlitchTip — Self-Hosted Sentry  (WAVE-2 PLANNED)
Sentry-API compatible, runs as a Docker service on amina, free OSS.
sentry-sdk[fastapi] + @sentry/nextjs both point at the GlitchTip URL.
Captures unhandled exceptions w/ call_id tag.

5.5  Prometheus + Loki + Promtail + Grafana ("LGTM-lite")  (WAVE-2 PLANNED)
Single docker-compose.observability.yml overlay adds:
  Prometheus (scrape /metrics every 15s)
  prometheus-fastapi-instrumentator (route latency + RPS counters)
  Promtail (tail Docker stdout → Loki)
  Loki (log store + LogQL search)
  Grafana (4 seed dashboards: Pipeline · LLM · API · Errors)

5.6  Debug Workflow (target after Wave 2)
  1. GlitchTip alert      → exception type + stack + call_id tag
  2. Grafana Loki         → {compose_service="compliance-backend"} |= "call_id=..."
                            → see every step the call took
  3. Prometheus           → systemic vs isolated (LLM provider 429? one rule? )
  4. Inngest dashboard    → confirm run state + retry count + which step


6. Tools Summary & Alternatives

Layer                       Chosen (current)              Spec doc proposed         Why we diverged
─────────────────────────────────────────────────────────────────────────────────────────────────
API Framework               FastAPI 0.115                 FastAPI                   match
Frontend Framework          Next.js 16 + React 19         "React"                   richer console
Edge / TLS                  Cloudflare Tunnel             AWS API Gateway / Nginx   no public ports, no Nginx
Message Broker              Inngest event bus             RabbitMQ                  collapses 3 services into 1
Task Queue                  Inngest durable functions     Celery (2 worker pools)   single function in v1
Result Backend / Checkpoints Inngest step memo + Call row Redis                     no Redis in stack
Database                    Supabase Postgres             Postgres (Docker)         managed; same Postgres
ORM / Migrations            SQLAlchemy 2.0 + Alembic      same                      match
Vector Search               pgvector (Postgres extension) text-embedding-3-small    same model, hosted on Postgres
Object Storage              Supabase Storage              S3 / Hetzner Object       SDK direct, not S3 protocol
Audio Transcription         Tribunal: Deepgram +          AssemblyAI single-engine  accuracy moat
                            AssemblyAI + Speechmatics +
                            Groq + OpenAI + Cohere + Gemini
LLM (rule analysis)         Gemini 2.5 Flash → Sonnet 4.6 Claude Haiku flat         tiered escalation > flat
Agent Loop                  Smart Agent (tool-using,      n/a (spec had no agents)  RAG-grounded reviewer aid
                            max 8 turns) + RAG tools
LLM Gateway                 OpenRouter / Anthropic /      direct Anthropic SDK      multi-provider failover
                            OpenAI / Gemini direct
HITL Console                /hitl reviewer queue + claim/release locks   n/a       core product, not in spec
Compliance Replay           POST /calls/:id/reanalyze (Wave-3 planned)   spec hint  uses stored transcript
Audit Log                   audit_log (hash chain) ★      audit_log (3 columns)     tamper-evident chain
Failed-Job Forensics        failed_jobs ★ + watchdog      n/a                       Wave-1 new
Error Tracking              GlitchTip self-host (Wave-2)  Sentry SaaS              OSS, $0
Log Aggregation             Loki + Promtail (Wave-2)      Loki                      match
Metrics                     Prometheus + Grafana (Wave-2) Prometheus + Grafana      match
Task Monitor                Inngest Dashboard             Flower                    Inngest covers
Reverse Proxy               Cloudflare Tunnel             Nginx + Let's Encrypt    no certbot needed
Container Orchestration     Docker Compose                Docker Compose            match
IaC                         OpenTofu (Cloudflare DNS only) Terraform (full)         Contabo provider too thin
Infra Provider              Contabo VPS (161.97.178.185)  Hetzner cx41              client constraint
CI                          GitHub Actions ★              GitHub Actions            ★ Wave-1 wired
                            (test.yml + coverage.yml +
                            touched-fns-gate.yml)
PR Hygiene                  PR template + branch protect  unspecified               ★ Wave-1
Test Discipline             pytest + vitest + Playwright  pytest + e2e             match (CI gated ★)
Backups                     Supabase managed (Wave-3      pg_dump + S3              Supabase Pro PITR
                            adds pg_dump nightly cron)


7. Missing Layers from suggested-arch.md  /  Alternatives We Use

These are the layers from the spec doc where current code does NOT match:

  Spec:  RabbitMQ message broker
  Ours:  Inngest event bus (managed)
         Skipped retrofit. Same durability via at-least-once + step memo.

  Spec:  Celery (worker_pipeline + worker_analysis pools)
  Ours:  Single Inngest durable function process_call
         Two-pool isolation deferred until starvation measured.

  Spec:  Redis (result backend + checkpoints)
  Ours:  Inngest step memoization + Call row state columns
         No Redis container on the VPS.

  Spec:  S3-compatible object storage with endpoint env-var swap
  Ours:  Supabase Storage SDK direct (call-audio bucket)
         Wave-3 introduces StorageBackend ABC for portability.

  Spec:  Sentry SaaS
  Ours:  GlitchTip self-host (Wave-2 — not yet running on amina)
         Currently no error tracking; Inngest dashboard catches workflow
         failures, FastAPI uncaught exceptions hit only Loguru stdout.

  Spec:  Loki + Promtail + Prometheus + Grafana already running
  Ours:  Wave-2 plan; not yet shipped. Today only Loguru → Docker stdout.

  Spec:  Flower Celery monitor on :5555
  Ours:  Inngest Dashboard (functionally equivalent for our queue model).

  Spec:  Nginx + Let's Encrypt reverse proxy on the VPS
  Ours:  Cloudflare Tunnel (cloudflared) — no public 80/443 on the VPS,
         no certbot, no Nginx config.

  Spec:  Hetzner cx41 VPS
  Ours:  Contabo VPS at 161.97.178.185.
         Contabo Terraform provider too thin for full VM IaC; Wave-1 manages
         only Cloudflare DNS in OpenTofu and documents VPS lifecycle as an
         SSH + Docker Compose runbook.

  Spec:  Terraform (hetzner/aws/azure dirs)
  Ours:  OpenTofu (OSS Terraform fork), Cloudflare-only.
         AWS / Azure dirs not started — Phase 2 deferred.

These are the layers we have that suggested-arch.md does NOT mention:

  Smart Agent layer with tiered LLM escalation (app/agent/)
  RAG over rule_chunks + loa_chunks + supplier_doc_chunks (pgvector)
  HITL reviewer console (claim/release/lock_override + tamper-evident audit)
  Multi-engine STT consensus tribunal (5+ engines, WER benchmark suite)
  Vulnerable-customer detector (extraction/vulnerability.py, flag-gated)
  Pricing-mismatch detector (extraction/pricing.py, flag-gated)
  Field-source provenance tracking (field_sources.py)
  Deal lifecycle + composite verdict (deal_lifecycle / deal_verdict / deals_composite)
  Tracker XLSX import + export (openpyxl, tracker_routes / tracker_edit_routes)
  Rejection factory (rejection_factory.py)
  Script versioning + variants (script_routes.py)
  Customer + Directive + Saved-View management
  Inngest cron watchdog with exhaustion-handler (Wave-1 ★)
  failed_jobs forensic table + read API (Wave-1 ★)
  audit_log tamper-evident hash chain w/ read API (extended Wave-1 ★)
  CI required-checks: test.yml + coverage.yml + touched-fns-gate.yml (Wave-1 ★)


8. Durability — How "Won't Lose Jobs" Actually Works Today

Inngest at-least-once delivery
  └── if process_call raises mid-step, the run is retried with the same
      input. Step memoization means already-completed steps are NOT re-run.

Per-step _STEP_TIMEOUTS in process_call.py
  └── soft cap each step (e.g. transcribe 300s) under the watchdog's
      7-minute "stuck" threshold so a hung provider trips locally first.

redispatch_watchdog cron (every minute)
  └── _STUCK_QUERY  picks runs with watchdog_redispatch_count < 1, redispatches
  └── _EXHAUSTED_QUERY (Wave-1 ★) picks runs that already burned their one
      redispatch and are still stuck → calls _handle_exhausted_run

_handle_exhausted_run
  └── reads Call.last_step_name + last_step_error
  └── INSERT into failed_jobs (idempotent on (call_id, attempts) unique index)
  └── flips Call.status = 'failed'
  └── reviewer sees the row in /api/observability/failed-jobs and replays
      via the (Wave-3) /reanalyze endpoint

audit_log hash chain
  └── every mutating route writes a row with this_hash linked to prev_hash
  └── readable at /api/observability/audit
  └── tamper-evident: any retroactive edit invalidates downstream this_hash


9. Deployment Strategy — Today and Phase-2 Notes

Today  (Phase 1 — Contabo, NOT Hetzner)
  Contabo VPS at 161.97.178.185, behind Cloudflare Tunnel
  Docker Compose manages all services on one host:
    compliance-backend   :8001  (FastAPI)
    compliance-frontend  :9000  (Next.js standalone)
    cloudflared          (Cloudflare Tunnel client)
    pgvector test DB     :5433  (local dev only — prod uses Supabase cloud)
  Postgres + Storage    Supabase managed (eu-west-1 pooler)
  Inngest               Cloud free tier (event bus + dashboard)
  No Nginx · No RabbitMQ · No Redis · No Celery · No Flower

Wave-2 will add (single overlay file docker-compose.observability.yml):
  glitchtip      :8080    error tracking (Sentry-API compatible)
  prometheus     :9090    metrics scrape
  grafana        :3001    dashboards
  loki           :3100    log store
  promtail       (Docker socket reader)

Wave-3 will add:
  pg_dump_cron sidecar (alpine + cron) → encrypted dated tarball to
  Supabase Storage backups/ bucket; 7-day retention; documented restore drill

Phase 2 — AWS / Azure (deferred)
  Spec proposes ECS Fargate + RDS + Amazon MQ + ElastiCache. Migration cost
  on current code is small (env vars only) for Postgres but real (code change)
  for object storage because we use Supabase SDK, not S3 protocol. Wave-3's
  StorageBackend ABC closes that gap.


10. Infrastructure as Code — OpenTofu (Cloudflare DNS only)

  infrastructure/contabo/
  ├── versions.tf       OpenTofu + Cloudflare provider versions
  ├── variables.tf      cloudflare_*, vps_ipv4, subdomain
  ├── dns.tf            cloudflare_record.compliance_apex (A → vps_ipv4, proxied)
  ├── README.md         SSH + Docker Compose runbook for the VPS itself
  ├── .gitignore        ignore tfstate, tfvars
  └── (no main.tf — Contabo provider too thin to manage VM lifecycle)

  Why no Hetzner / no full VM IaC
  Production runs on Contabo, not Hetzner. The Contabo Terraform provider
  covers basic instance ops only — no firewall primitive, no proper
  network resources. Trying to scaffold full VM IaC against it produces
  fragile state. The VPS lifecycle is therefore documented as an SSH +
  Docker Compose runbook (infrastructure/contabo/README.md) and
  Cloudflare DNS — the one piece worth IaC — is the only Terraform-managed
  resource.

  Why Cloudflare DNS in OpenTofu
  DNS is the single resource a typo or mis-merge can destroy quickly.
  IaC + zero-diff `tofu plan` gate before merge keeps it safe.

  Phase-2 ready
  Adding infrastructure/aws/ and infrastructure/azure/ later is additive,
  not a rewrite.


11. Environment Variable Abstraction (Pydantic Settings)

backend/app/config.py uses pydantic-settings with .env file loading. Same
contract as the spec doc — every infrastructure URL or API key is an env var.

  # .env — current (Contabo + Supabase + Inngest cloud)
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

  # .env — local test (Docker pgvector)
  DATABASE_URL=postgresql+psycopg2://postgres:test@localhost:5433/compliance_test
  DEEPGRAM_API_KEY=stub
  SUPABASE_URL=
  ...

  Rule (unchanged from spec)
  If it points to infrastructure, it is an env var. If it tunes runtime
  behavior (LLM model, escalation threshold, feature flag), it is also
  an env var (config.py exposes them as Pydantic fields with defaults).


12. Cost Model — Today

Line                                   Monthly
─────────────────────────────────────────────────
Contabo VPS (amina)                    ~$10–25
Supabase Free tier (Pro $25 if PITR)   $0–25
Inngest free tier (≤50k runs/mo)       $0
Cloudflare Tunnel + DNS                $0
GlitchTip self-host (Wave-2)           $0 (RAM cost absorbed)
Loki+Promtail+Prom+Grafana (Wave-2)    $0 (RAM cost absorbed)
GitHub Actions (private, free 2k min)  $0
OpenTofu                               $0
Per-call STT tribunal                  variable (dominant)
Per-call LLM (Sonnet escalations)      variable (dominant after Wave-4 cuts)
Per-call OpenAI embeddings (RAG)       near-free
─────────────────────────────────────────────────
Fixed monthly floor                    ~$10–50
Per-call variable                      tribunal + LLM dominate

  Where the spec's "$25–35/mo" envelope hits today
  Achievable only with single-engine STT (e.g. Deepgram only) and
  Gemini-Flash-only LLM (no Sonnet escalation). Current production runs
  the tribunal + tiered Sonnet, so per-call cost is the dominant variable.
  Wave-4's embedding pre-filter + escalation-threshold tuning brings
  blended cost back toward the spec's envelope without losing accuracy.


13. Wave-1 Summary (what shipped May 2026)

Branch                      feat/wave1-foundation
Commits                     23
Tasks completed             10 of 11 (T8c skipped — rules in static JSON)
Tests added                 28+ green
  · CI required-checks      test.yml + coverage.yml + touched-fns-gate.yml
  · PR template             touched-functions + tests-added checkboxes
  · failed_jobs table       Alembic mig + ORM + writer + watchdog wire
  · /observability/audit    + /failed-jobs read APIs
  · record_audit() coverage routes.py · hitl_routes.py · script_routes.py
                            · deals_routes.py (rules N/A)
  · AuditLog ORM            added so SQLite tests can create_all the table
  · IaC scaffolding         infrastructure/contabo/ (Cloudflare DNS only)
  · Spec + plan + progress  reflect Contabo pivot, PAT rotation prereq



End of Document
