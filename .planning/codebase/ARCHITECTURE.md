# Architecture

**Analysis Date:** 2025-01-09

## Pattern Overview

**Overall:** Modular monolith with durable event-driven pipeline + HITL reviewer console

**Key Characteristics:**
- Async-first FastAPI backend with Inngest Cloud for durability
- Event-sourced compliance processing pipeline (6 discrete steps, memoized across retries)
- Multi-engine transcription tribunal (7 engines, consensus WER picking)
- Tiered smart agent layer (Gemini Flash → Claude Sonnet escalation)
- HITL reviewer queue with claim/release locks and audit-log hash chain
- Next.js 16 App Router frontend with same-origin API proxy
- Single Postgres database (Supabase) + object storage (Supabase) for all persistence
- Inngest step memoization replaces Redis checkpoint store
- No separate message broker; Inngest IS the event bus

## Layers

**HTTP/REST Layer:**
- Purpose: Route incoming requests, validate auth, delegate to service layer
- Location: `backend/app/*_routes.py` (25+ routers mounted in `main.py`)
- Contains: FastAPI route handlers with Pydantic request/response schemas
- Depends on: Auth (Supabase JWT), session DB, service functions
- Used by: Frontend via next.config.mjs rewrites + direct API clients

**Service/Business Logic Layer:**
- Purpose: Implement compliance scoring, pipeline steps, call analysis
- Location: `backend/app/` (analysis.py, compliance.py, deal_lifecycle.py, pipeline.py, rejection_factory.py, etc.)
- Contains: Core algorithms (derive_compliance, build_rejection_for_call), transcription tribunal (tribunal_wer.py), extraction detectors (extraction/*.py)
- Depends on: Models, storage, LLM providers, Inngest
- Used by: Routes, workflows

**Workflow/Durability Layer:**
- Purpose: Coordinate multi-step async processing with checkpoints + retries
- Location: `backend/app/workflows/process_call.py` + `rag_ingest.py` + `redispatch_watchdog.py`
- Contains: Inngest functions wrapped with ctx.step.run(...), per-step timeouts, error tracking
- Depends on: Service layer, Inngest client, Postgres
- Used by: Inngest Cloud scheduler + HTTP event webhooks

**Smart Agent Layer:**
- Purpose: Tool-using LLM loop for rule analysis with escalation path
- Location: `backend/app/agent/agent_loop.py` + `tool_handlers.py` + `tools.py` + `playbooks.py`
- Contains: System/user prompt builders, OpenRouter/Gemini/Claude routing, tool binding (search_rules, search_loas, get_rejection_rules, etc.), escalation logic
- Depends on: Config (active_provider), RAG search, rules/LOA/supplier doc chunks, LLM retry decorator
- Used by: checkpoint_analyzer.py (step 4 of pipeline)

**RAG/Vector Search Layer:**
- Purpose: Semantic search over compliance rules, LOAs, supplier documents
- Location: `backend/app/rag/` (embed.py, search.py, ingest.py)
- Contains: pgvector cosine similarity queries, embedding prefilter (Wave-4 feature flag)
- Depends on: Postgres pgvector extension, OpenAI text-embedding-3-small or Gemini embeddings
- Used by: Smart agent tools, checkpoint analyzer

**HITL/Reviewer Layer:**
- Purpose: Claim/release call queue, score review, lock management, audit trails
- Location: `backend/app/hitl_routes.py` (86KB, core product)
- Contains: Queue queries with filter/sort, claim-lock logic with expiry sweep, review-session state, score/flag override endpoints
- Depends on: Auth (current_user), models (Call, ReviewSession, ClaimLock, Profile), audit logging
- Used by: Reviewer frontend (queue.tsx, call detail pages)

**Data/Persistence Layer:**
- Purpose: ORM mapping + schema management
- Location: `backend/app/models.py` (55KB) + `backend/alembic/versions/` (60+ migrations)
- Contains: SQLAlchemy ORM definitions (Call, CallCheckpoint, Customer, Deal, Rule*, Rejection, etc.), Alembic versioning
- Depends on: Postgres (Supabase), pgvector, SQLAlchemy 2.0
- Used by: All routes + workflows + services

**Storage Abstraction Layer:**
- Purpose: Backend-agnostic audio upload/download + signed URLs
- Location: `backend/app/storage/` (supabase.py + config-selected backend)
- Contains: Upload to Supabase Storage bucket "call-audio", download to temp, signed URL generation
- Depends on: Settings (storage_backend flag), Supabase SDK or S3 client
- Used by: Routes (upload), workflows (download in step 1)

**Observability Layer:**
- Purpose: Structured logging, error tracking, metrics, audit trails
- Location: `backend/app/logger.py`, `observability_routes.py`, `audit.py`, `observability_metrics.py`
- Contains: Loguru JSON logging (bound with call_id/step), custom audit-log read APIs, Prometheus instrumentator, hash-chain audit_log writes
- Depends on: Sentry (optional, Wave-2), Prometheus, structlog bindings
- Used by: All layers (logging), routes (audit reads)

**Frontend/UI Layer:**
- Purpose: Reviewer console, admin settings, intake forms
- Location: `frontend-v3/src/` (Next.js 16 App Router)
- Contains: Pages (queue, calls, hitl, tracker, rules, agents, observability), components (design system + business logic), API client, mutations/queries
- Depends on: Supabase Auth (JWT), API (next.config rewrites), TanStack Query, Zustand
- Used by: End-users (reviewers, admins, leads)

## Data Flow

**Call Processing Pipeline (6 steps, Inngest-orchestrated):**

1. **Upload** (HTTP POST /api/calls/upload)
   - Frontend sends multipart/form-data (audio file)
   - Routes validates format signature, persists to Supabase Storage, creates Call row (status: queued)
   - Emits Inngest event "call/uploaded" if use_inngest_pipeline=True
   - Returns call_id immediately (non-blocking)

2. **Download Audio** (Workflow step 1, timeout 120s)
   - ctx.step.run wraps Supabase Storage.download
   - Saves to /tmp, calculates duration
   - Stores path in workflow state for next step
   - Retry via memoization skips re-download

3. **Transcribe** (Workflow step 2, timeout 300s)
   - asyncio.gather spans 7 engines: Deepgram, AssemblyAI, Speechmatics, Groq, OpenAI, Cohere, Gemini
   - tribunal_wer.py picks consensus (highest WER match), tags field_source
   - Speaker diarization → natural chunk boundaries
   - Stores on Call.transcript, Call.word_data

4. **Detect Metadata** (Workflow step 3, timeout 60s)
   - Agent name + supplier detection (fuzz match against registered agents)
   - Customer name detection + deal fuzzy match
   - Filename rename to normalized pattern
   - Updates Call.agent_name, detected_supplier, customer_name

5. **Analyze Checkpoints** (Workflow step 4, timeout 420s, CPU-intensive)
   - Fetch rules for script + supplier variant
   - Smart Agent loop (app/agent/agent_loop.py):
     - Gemini 2.5 Flash first pass (cheap, fast)
     - Escalate to Claude Sonnet 4.6 if confidence == low (user-tunable threshold)
     - Max 8 tool-use turns per batch before forcing verdict
   - Tool set: search_rules, search_loas, search_supplier_docs, get_rejection_rules
   - Delete-then-insert CallCheckpoint rows (idempotent on retry)
   - Extraction layer triggers: vulnerable_customer detector, pricing_mismatch detector
   - Updates Call.risk_tags (TEXT[])

6. **Score** (Workflow step 5, timeout 60s)
   - derive_compliance(call) → (score, status, reason)
   - Dedupe checkpoints by rule_id (highest confidence wins)
   - Flag missing required checkpoints
   - Updates Call.score, Call.compliance_status

7. **Finalize** (Workflow step 6, timeout 30s)
   - build_rejection_for_call() constructs rejection records
   - deal_lifecycle / deal_verdict updates deal-level rollup
   - emit "call/finalized" event for RAG ingest trigger
   - Flip Call.status from "processing" → "completed"
   - Update Call.completed_at

**HITL Reviewer Flow:**

1. Queue page loads → useQueueQuery (TanStack Query)
   - GET /api/calls/queue?filters (unclaimed/in_review/today/all)
   - Backend sorts by risk score + created_at, returns paginated list

2. Reviewer selects call → useCallDetailQuery
   - GET /api/calls/{id}
   - Returns full Call + CallCheckpoints + metadata

3. "Claim & Review" → useClaimCall mutation
   - POST /api/calls/{id}/claim (includes reviewer_id)
   - Backend: Check no existing claim, create ClaimLock row with expiry
   - Update Call.review_status = "in_review"
   - record_audit() writes audit_log row (hash chain)

4. Review session (claim lock auto-released after 120 min idle)
   - Reviewer reads transcript, listens to audio
   - Can override checkpoint scores via PATCH /api/calls/{id}/checkpoints/{checkpoint_id}
   - Can set compliance verdict (compliant/non-compliant/escalate)
   - record_audit() on every mutation

5. Idle lock sweep (120s cron in main.py lifespan)
   - _idle_release_loop() queries for expired claims (now > created_at + 120min)
   - Releases lock so next reviewer can claim

**RAG Ingestion (Triggered on call/finalized + script/changed):**

1. rag_ingest_call_fn / rag_ingest_script_fn listens for events
2. Extracts relevant rules/LOAs/supplier docs from Call or Script
3. Batches chunks (max 512 tokens each)
4. Embeds via Gemini or OpenAI embedding API
5. Upserts into rule_chunks / loa_chunks / supplier_doc_chunks with vector

## Key Abstractions

**Call — Central domain entity:**
- Purpose: Represents a single recorded sales call to be analyzed
- Examples: `backend/app/models.py:Call`
- Pattern: SQLAlchemy ORM row, updated atomically at pipeline step boundaries
- Fields: id (UUID), filename, status, transcript, score, compliance_status, risk_tags, audio_storage_key, agent_name, detected_supplier, customer_deal_id, review_status, claim_locked_at, claimed_by, last_step_name, last_step_error

**CallCheckpoint — Rule analysis result:**
- Purpose: Verdict for a single rule/checkpoint against one call
- Examples: `models.CallCheckpoint`
- Pattern: Inserted in batch (delete-then-insert, idempotent on retry)
- Fields: call_id, rule_id, status (pass/fail/escalate), confidence, evidence, reason, agent_source (which agent took the call), field_sources (provenance)

**Smart Agent — Tool-using LLM orchestrator:**
- Purpose: Analyze checkpoints with escalation path
- Examples: `agent/agent_loop.py:run_agent_loop()`
- Pattern: Async generator of tool calls + final verdict
- Key: Gemini Flash default, Claude Sonnet on low confidence, max 8 turns

**StorageBackend — Provider-agnostic audio storage:**
- Purpose: Abstract over Supabase Storage vs. S3
- Examples: `storage/supabase_backend.py` (current), `storage/s3_backend.py` (Wave-3)
- Pattern: Protocol with upload, download, signed_url methods
- Wave-3 adds SelectableBackend per STORAGE_BACKEND env var

**HITL Reviewer — Auth + claim-lock state machine:**
- Purpose: Track which reviewer owns which call, expire stale claims
- Examples: `hitl_routes.py:claim_call()`, `main.py:_idle_release_loop()`
- Pattern: ClaimLock row with reviewer_id + created_at, swept every 120s for expiry
- Key: Prevents concurrent review, audit-logged on claim/release

**Pipeline — Durable orchestrator:**
- Purpose: Wrap six steps in Inngest ctx.step.run(...) memoization
- Examples: `workflows/process_call.py:process_call()`
- Pattern: Each step reads/writes Call row atomically
- Idempotency: Checkpoint analysis uses delete-then-insert; others are naturally idempotent

## Entry Points

**HTTP Upload (routes.py:POST /api/calls/upload):**
- Location: `backend/app/routes.py:upload_call()`
- Triggers: File validation, Supabase Storage persist, Call row creation, Inngest event emit (if flag on)
- Responsibilities: Multipart parsing, audio signature check, DB transaction, audit log

**Inngest Webhook (main.py:serve()):**
- Location: `backend/app/main.py` line 230 (inngest.fast_api.serve)
- Triggers: Handles Inngest Cloud delivery of "call/uploaded", "call/reanalyze", "script/changed" events
- Responsibilities: Route event to correct durable function (process_call, rag_ingest_*, redispatch_watchdog)

**CLI Script Routes (script_routes.py):**
- Location: `backend/app/script_routes.py`
- Triggers: Bulk operations (import scripts, update rules), admin endpoints
- Responsibilities: Batch validation, permission checks (X-Admin-Key header)

**Reviewer Pages (frontend-v3):**
- Location: `frontend-v3/src/app/(reviewer)/queue/page.tsx`, `/calls/[id]/`
- Triggers: Browser navigation
- Responsibilities: Load queue, claim call, edit scores, release claim

## Error Handling

**Strategy:** Layered with Inngest as safety net; service layer raises, routes catch and return HTTP codes

**Patterns:**

- **LLM transient errors (429, timeout):** `resilience.py:LLM_RETRY` decorator (exponential backoff, 3 retries)
- **Service errors:** Raise `Exception`, caught in route handler, returned as 400/500 + logged
- **Workflow step errors:** Caught by `ctx.step.run()` context, Inngest retries with backoff; after max retries → `_handle_exhausted_run()` flips status=failed + writes failed_jobs row
- **Database errors:** Caught at SessionLocal.close(), logged with call_id context, re-raised
- **Storage errors:** Caught in storage backend, re-raised as RuntimeError with path context

**Per-step soft timeouts** (process_call.py:_STEP_TIMEOUTS) catch hung providers before Inngest's 7-min watchdog:
- download_audio 120s, transcribe 300s, detect_metadata 60s, analyze_checkpoints 420s, score 60s, finalize 30s

## Cross-Cutting Concerns

**Logging:**
- Approach: Loguru JSON to stdout, bound with call_id + step + attempt
- Example: `app_log.bind(call_id=call_id, step="transcribe").info("WORKFLOW_STEP status=ok duration_ms=...")`
- Consumed by: Docker log driver (development), Loki (Wave-2 planned)

**Validation:**
- Approach: Pydantic schemas at route boundaries + Alembic-enforced DB constraints
- Example: `CallListResponse`, `CallResponse` in `schemas.py`
- No runtime validation inside service layer (assume validated on entry)

**Authentication:**
- Approach: Supabase JWT via current_user dependency
- Example: `reviewers.py:current_reviewer()` extracts user claims from JWT
- Routes: Protected routes inject `user=Depends(current_reviewer)`

**Audit Trail:**
- Approach: Hash-chain audit_log for tamper-evidence
- Example: Every mutation writes audit_log row with prev_hash → this_hash
- Verification: Readable at GET /api/observability/audit; downstream validator can detect retroactive edits

---

*Architecture analysis: 2025-01-09*
