# Codebase Structure

**Analysis Date:** 2025-01-09

## Directory Layout

```
compliance-agent-feat-wave5-deploy/
├── backend/
│   ├── app/
│   │   ├── main.py                      # FastAPI app init + lifespan + router mounting
│   │   ├── config.py                    # Pydantic Settings (env-backed)
│   │   ├── database.py                  # SQLAlchemy engine + SessionLocal
│   │   ├── models.py                    # ORM definitions (55KB, 20+ tables)
│   │   ├── database/
│   │   │   └── (no subdirs — models.py is monolithic)
│   │   │
│   │   ├── *_routes.py                  # 25+ routers (auth, HITL, deals, etc.)
│   │   │   ├── routes.py                # Main: uploads, status, replay
│   │   │   ├── hitl_routes.py           # HITL queue, claims, overrides (86KB)
│   │   │   ├── agents_routes.py         # Agent profile management
│   │   │   ├── agent_chat_routes.py     # /api/agent/chat SSE endpoint
│   │   │   ├── customers_routes.py      # Customer CRUD
│   │   │   ├── deals_routes.py          # Deal lifecycle
│   │   │   ├── directives_routes.py     # Directive management
│   │   │   ├── rules_routes.py          # Rule catalog browser
│   │   │   ├── script_routes.py         # Script versioning
│   │   │   ├── rejections_routes.py     # Rejection workflow
│   │   │   ├── tracker_routes.py        # XLSX tracker board reads
│   │   │   ├── tracker_edit_routes.py   # XLSX tracker inline edits
│   │   │   ├── import_xlsx_routes.py    # Bulk XLSX import
│   │   │   ├── observability_routes.py  # Audit, failed-jobs, stuck queries
│   │   │   ├── rag_routes.py            # RAG search (reviewer aid)
│   │   │   ├── rag_admin_routes.py      # RAG admin (chunk upload)
│   │   │   ├── email_routes.py          # Confirmation email (W3.B)
│   │   │   ├── flags_routes.py          # Flag definitions
│   │   │   └── saved_views_routes.py    # Saved queue filters
│   │   │
│   │   ├── agent/                       # Smart agent layer
│   │   │   ├── agent_loop.py            # Core tool-using LLM loop
│   │   │   ├── tool_handlers.py         # Tool execution context + routing
│   │   │   ├── tools.py                 # Tool definitions (search_rules, etc.)
│   │   │   ├── playbooks.py             # System prompts by supplier
│   │   │   └── feedback.py              # Agent learning storage
│   │   │
│   │   ├── extraction/                  # Data extraction detectors
│   │   │   ├── pricing.py               # Pricing-mismatch detector (flag)
│   │   │   ├── vulnerability.py         # Vulnerable-customer detector (flag)
│   │   │   └── __init__.py
│   │   │
│   │   ├── rag/                         # Vector search + semantic retrieval
│   │   │   ├── embed.py                 # Embedding pipeline (Gemini or OAI)
│   │   │   ├── search.py                # pgvector cosine similarity queries
│   │   │   ├── ingest.py                # Chunk batch ingestion
│   │   │   └── __init__.py
│   │   │
│   │   ├── storage/                     # Storage backend abstraction
│   │   │   ├── supabase.py              # Current: Supabase Storage (boto3-less)
│   │   │   └── __init__.py              # Wave-3: s3.py + SelectableBackend
│   │   │
│   │   ├── workflows/                   # Inngest durable functions
│   │   │   ├── process_call.py          # 6-step pipeline orchestrator
│   │   │   ├── rag_ingest.py            # call/finalized → embedding ingest
│   │   │   ├── redispatch_watchdog.py   # Cron: stuck run recovery
│   │   │   ├── pg_dump_nightly.py       # Cron: database backup
│   │   │   ├── events.py                # Event type enums
│   │   │   ├── observability.py         # Step logging + Prometheus
│   │   │   └── __init__.py
│   │   │
│   │   ├── glossaries/                  # Static business data
│   │   │   ├── agents.json              # Registered agent names + IDs
│   │   │   └── suppliers.json           # Supplier catalog
│   │   │
│   │   ├── templates/                   # Jinja2 email templates
│   │   │   └── confirmation_email.html
│   │   │
│   │   ├── intake/                      # (stub, for future)
│   │   │
│   │   ├── analysis.py                  # Old batch analyzer (legacy path)
│   │   ├── audit.py                     # Hash-chain audit_log writer
│   │   ├── auth.py                      # Supabase JWT validation
│   │   ├── business_detect.py           # Business name fuzzy matching
│   │   ├── checkpoint_analyzer.py       # Batch checkpoint orchestrator (pre-agent)
│   │   ├── checkpoint_filter.py         # Rule pre-filtering
│   │   ├── compliance.py                # derive_compliance() scoring logic
│   │   ├── deal_lifecycle.py            # Deal state transitions
│   │   ├── deal_verdict.py              # Deal-level aggregation
│   │   ├── deals_composite.py           # Multi-deal rollups
│   │   ├── field_sources.py             # Provenance tracking
│   │   ├── inngest_client.py            # Inngest client initialization
│   │   ├── logger.py                    # Loguru configuration
│   │   ├── observability_metrics.py     # Prometheus custom metrics
│   │   ├── pipeline.py                  # Legacy in-process pipeline
│   │   ├── prompts.py                   # LLM prompt templates (28KB)
│   │   ├── rejection_factory.py         # Rejection record builder
│   │   ├── replays.py                   # Reanalyze endpoint logic
│   │   ├── resilience.py                # Retry decorators (LLM_RETRY)
│   │   ├── reviewers.py                 # Auth dependency (current_reviewer)
│   │   ├── schemas.py                   # Pydantic request/response models
│   │   ├── transcription.py             # Multi-engine orchestration
│   │   ├── tribunal_wer.py              # Consensus picking logic
│   │   ├── verification.py              # Fuzzy matching utilities
│   │   ├── import_xlsx_tracker.py       # XLSX parse + model binding
│   │   ├── events.py                    # (old, now in workflows/)
│   │   └── __init__.py
│   │
│   ├── alembic/                         # Database migrations (Alembic)
│   │   ├── versions/                    # 60+ migration files
│   │   │   ├── 9b04b2ff3b4c_initial_schema.py
│   │   │   ├── 243544911129_hitl_tables_and_call_columns.py
│   │   │   ├── 6c863e1ce3b1_failed_jobs.py
│   │   │   ├── 497bd38e5551_organizations_and_audit_log_hardening.py
│   │   │   └── (56 more...)
│   │   ├── env.py                       # Alembic runtime config
│   │   ├── script.py.mako               # Migration template
│   │   └── alembic.ini
│   │
│   ├── tests/                           # pytest suite
│   │   ├── conftest.py                  # Pytest fixtures (in-memory SQLite DB)
│   │   ├── test_*.py                    # Unit + integration tests
│   │   └── (benchmark suite if present)
│   │
│   ├── requirements.txt                 # Python dependencies
│   ├── pyproject.toml                   # Poetry config (if used)
│   └── Dockerfile                       # Build image for backend
│
├── frontend-v3/                         # Next.js 16 App Router (React 19)
│   ├── src/
│   │   ├── app/                         # App Router pages
│   │   │   ├── layout.tsx               # Root layout (Sentry + ThemeProvider)
│   │   │   ├── (auth)/                  # Auth group
│   │   │   │   ├── layout.tsx
│   │   │   │   └── login/
│   │   │   │       └── page.tsx
│   │   │   ├── (reviewer)/              # Reviewer routes (claim/release)
│   │   │   │   ├── queue/
│   │   │   │   │   └── page.tsx         # Master-detail HITL queue
│   │   │   │   ├── calls/
│   │   │   │   │   ├── page.tsx         # Call list
│   │   │   │   │   └── [id]/
│   │   │   │   │       └── page.tsx     # Call detail + claim
│   │   │   │   ├── findings/
│   │   │   │   │   └── page.tsx
│   │   │   │   └── layout.tsx
│   │   │   ├── (admin)/                 # Admin routes
│   │   │   │   ├── agents/              # Agent profile list
│   │   │   │   │   ├── page.tsx
│   │   │   │   │   └── [name]/page.tsx
│   │   │   │   ├── calls/               # Admin call list
│   │   │   │   │   └── page.tsx
│   │   │   │   ├── customers/           # Customer CRUD
│   │   │   │   ├── deals/               # Deal detail pages
│   │   │   │   ├── rules/               # Rule catalog browser
│   │   │   │   ├── scripts/             # Script management
│   │   │   │   ├── rejections/          # Rejection workflow UI
│   │   │   │   ├── tracker/             # XLSX tracker board
│   │   │   │   ├── observability/       # Audit log + failed jobs
│   │   │   │   ├── portal-batches/      # Batch operations
│   │   │   │   ├── compliant/           # Filtered calls
│   │   │   │   ├── non-compliant/       # Filtered calls
│   │   │   │   ├── settings/            # Admin config
│   │   │   │   └── layout.tsx
│   │   │   └── api/
│   │   │       └── health-check/
│   │   │           └── route.ts         # Liveness probe
│   │   │
│   │   ├── components/
│   │   │   ├── design/                  # Design system (pixel-perfect handoff)
│   │   │   │   ├── Avatar.tsx
│   │   │   │   ├── Pill.tsx
│   │   │   │   ├── FilterChip.tsx
│   │   │   │   ├── ScoreBar.tsx
│   │   │   │   ├── Waveform.tsx
│   │   │   │   ├── EmptyState.tsx
│   │   │   │   └── ScreenFrame.tsx
│   │   │   ├── intake/                  # Intake form components
│   │   │   │   ├── L7Form.tsx           # File + deal selector
│   │   │   │   ├── SupplierCombobox.tsx
│   │   │   │   └── MetadataMismatchBanner.tsx
│   │   │   ├── reviewer/                # HITL-specific components
│   │   │   │   ├── FlagBadge.tsx
│   │   │   │   └── ScoreBar.tsx
│   │   │   ├── shared/                  # Reusable business logic
│   │   │   │   ├── CallPreviewPanel.tsx
│   │   │   │   ├── CursorPagination.tsx
│   │   │   │   └── ScoreGauge.tsx
│   │   │   ├── ui/                      # shadcn/ui primitives (generated)
│   │   │   │   ├── button.tsx
│   │   │   │   ├── input.tsx
│   │   │   │   ├── card.tsx
│   │   │   │   ├── dialog.tsx
│   │   │   │   ├── select.tsx
│   │   │   │   ├── form.tsx
│   │   │   │   ├── dropdown-menu.tsx
│   │   │   │   ├── badge.tsx
│   │   │   │   ├── combobox.tsx
│   │   │   │   ├── popover.tsx
│   │   │   │   └── label.tsx
│   │   │   ├── providers/               # Context + hooks
│   │   │   │   ├── QueryProvider.tsx    # TanStack Query client
│   │   │   │   └── ThemeProvider.tsx    # Dark/light mode
│   │   │   ├── Sidebar.tsx              # Main navigation
│   │   │   └── __init__.ts
│   │   │
│   │   ├── lib/                         # Utilities + data layer
│   │   │   ├── api.ts                   # apiFetch helper + error handling
│   │   │   ├── api-types.ts             # Generated/curated TS interfaces
│   │   │   ├── supabase.ts              # Supabase client + session
│   │   │   ├── auth.tsx                 # Auth context + useAuth hook
│   │   │   ├── mutations.ts             # TanStack Query mutation factory
│   │   │   ├── queries.ts               # TanStack Query query factory
│   │   │   ├── canonical-agents.ts      # Agent ID → name mapping
│   │   │   ├── canonical-supplier.ts    # Supplier ID → name mapping
│   │   │   ├── checkpoint-state.ts      # Client-side checkpoint state
│   │   │   ├── score.ts                 # Score parsing + formatting
│   │   │   ├── word-match.ts            # Transcript word highlighting
│   │   │   ├── utils.ts                 # Misc utilities
│   │   │   ├── hooks/                   # Custom React hooks
│   │   │   │   ├── useDebouncedValue.ts
│   │   │   │   ├── useUrlState.ts       # URL ↔ component state sync
│   │   │   │   └── (others)
│   │   │   ├── mutations/               # Query mutation builders
│   │   │   │   ├── reviewer.ts          # claim_call, release_call, etc.
│   │   │   │   ├── admin.ts             # rule, script, customer mutations
│   │   │   │   └── (others)
│   │   │   ├── queries/                 # Query builders
│   │   │   │   ├── reviewer.ts          # useQueueQuery, useCallDetailQuery
│   │   │   │   ├── admin.ts             # useRulesQuery, useCustomersQuery
│   │   │   │   └── (others)
│   │   │   └── schemas/                 # Zod validation schemas
│   │   │       ├── call.ts
│   │   │       ├── checkpoint.ts
│   │   │       └── (others)
│   │   │
│   │   ├── styles/
│   │   │   └── globals.css              # Tailwind setup + design tokens
│   │   │
│   │   └── __init__.ts
│   │
│   ├── public/                          # Static assets
│   │   ├── images/
│   │   └── icons/
│   │
│   ├── next.config.mjs                  # API proxy + Sentry integration
│   ├── sentry.*.config.ts               # Sentry error tracking config
│   ├── tsconfig.json
│   ├── tailwind.config.ts
│   ├── package.json
│   ├── package-lock.json
│   ├── Dockerfile                       # Standalone server build
│   └── AGENTS.md                        # (note: deprecations, read docs/)
│
├── infrastructure/
│   └── contabo/                         # IaC (Cloudflare DNS only)
│       ├── versions.tf                  # OpenTofu versions
│       ├── variables.tf                 # DNS variables
│       ├── dns.tf                       # Cloudflare records
│       ├── README.md                    # SSH + Docker Compose runbook
│       └── .gitignore
│
├── docker-compose.yml                   # Development: backend + frontend + DB stub
├── docker-compose.observability.yml     # Wave-2: GlitchTip + Prometheus + Loki + Grafana (overlay)
├── Dockerfile                           # (if monolithic)
│
├── current-arch.md                      # Architecture overview (maintained)
├── suggested-arch.md                    # Spec doc (Phase 1 baseline)
├── architecture-comparison.md           # Divergences from spec
│
├── .github/workflows/
│   ├── test.yml                         # pytest + coverage (Wave-1 required gate)
│   ├── coverage.yml                     # Coverage report upload
│   └── touched-fns-gate.yml             # PR gate: run tests for touched functions
│
└── .gitignore                           # Ignore .env, __pycache__, node_modules, .next, etc.
```

## Directory Purposes

**backend/app/:**
- Purpose: FastAPI application code
- Contains: Routes, models, services, workflows, storage, RAG
- Key files: `main.py` (entry point), `models.py` (ORM), `*_routes.py` (HTTP handlers)

**backend/app/agent/:**
- Purpose: Smart agent layer (tool-using LLM with escalation)
- Contains: Agent loop, tool definitions, playbooks (system prompts by supplier)
- Key files: `agent_loop.py` (core loop), `tools.py` (tool definitions)

**backend/app/extraction/:**
- Purpose: Data extraction detectors (pricing, vulnerability)
- Contains: Extraction pipelines, feature flags
- Key files: `pricing.py`, `vulnerability.py`

**backend/app/rag/:**
- Purpose: Vector search over compliance rules, LOAs, supplier docs
- Contains: Embedding, search, ingestion
- Key files: `search.py` (pgvector queries), `embed.py` (embedding pipeline)

**backend/app/storage/:**
- Purpose: Storage backend abstraction (Supabase vs. S3)
- Contains: Upload/download/signed_url implementations
- Key files: `supabase.py` (current), Wave-3 adds `s3.py` + selectable backend

**backend/app/workflows/:**
- Purpose: Inngest durable functions (event-driven orchestration)
- Contains: 6-step call pipeline, RAG ingest, watchdog cron
- Key files: `process_call.py` (6 steps), `redispatch_watchdog.py` (stuck recovery)

**backend/alembic/versions/:**
- Purpose: Database schema migrations (owned by Alembic, not hand-modified)
- Contains: 60+ migrations from initial schema to Wave-1
- Naming: `<hash>_<description>.py`, ordered by hash

**frontend-v3/src/app/:**
- Purpose: Next.js App Router pages (file-based routing)
- Contains: Route groups (auth, reviewer, admin), layouts, page components
- Key files: `(reviewer)/queue/page.tsx` (HITL queue), `(admin)/calls/page.tsx` (admin view)

**frontend-v3/src/components/:**
- Purpose: Reusable React components
- Contains: Design system (shadcn + pixel-perfect handoff), business logic components
- Key files: `design/` (UI atoms), `intake/` (upload form), `reviewer/` (HITL), `ui/` (shadcn primitives)

**frontend-v3/src/lib/:**
- Purpose: Utilities, API client, hooks, mutations, queries
- Contains: Data layer (TanStack Query), hooks, schemas, auth
- Key files: `api.ts` (apiFetch with JWT injection), `queries/` (useQueueQuery, etc.), `mutations/` (claim, release, etc.)

**infrastructure/contabo/:**
- Purpose: IaC for Cloudflare DNS (Phase 1 only)
- Contains: OpenTofu configurations (VPS lifecycle is SSH + Docker Compose runbook)
- Key files: `dns.tf`, `README.md`

## Key File Locations

**Entry Points:**

- `backend/app/main.py` — FastAPI app initialization, lifespan (DB warmup, idle lock sweep), router mounting, Inngest webhook registration
- `frontend-v3/src/app/layout.tsx` — Next.js root layout (Sentry, ThemeProvider, Sidebar)
- `backend/app/workflows/process_call.py` — Inngest function for 6-step call processing

**Configuration:**

- `backend/app/config.py` — Pydantic Settings (database_url, LLM provider, feature flags)
- `frontend-v3/next.config.mjs` — API proxy rewrites (/api/* → backend), Sentry integration
- `backend/alembic/alembic.ini` — Alembic runtime config (DB connection, migration discovery)

**Core Logic:**

- `backend/app/pipeline.py` — Legacy in-process pipeline (if use_inngest_pipeline=False)
- `backend/app/checkpoint_analyzer.py` — Batch checkpoint orchestration
- `backend/app/agent/agent_loop.py` — Smart agent tool-using loop (Gemini Flash → Sonnet escalation)
- `backend/app/compliance.py` — derive_compliance() scoring logic
- `backend/app/tribunal_wer.py` — Multi-engine transcription consensus picking
- `backend/app/rejection_factory.py` — Rejection record builder

**Testing:**

- `backend/tests/conftest.py` — Pytest fixtures (in-memory SQLite DB, Supabase mocks)
- `frontend-v3/playwright.config.ts` — Playwright E2E test config
- `.github/workflows/test.yml` — CI gate (pytest + coverage)

**HITL/Reviewer Workflows:**

- `backend/app/hitl_routes.py` — Queue, claim, release, lock expiry, score overrides (86KB core)
- `frontend-v3/src/app/(reviewer)/queue/page.tsx` — Master-detail HITL queue UI
- `backend/app/main.py:_idle_release_loop()` — 120s cron to sweep expired claims

**Audit & Observability:**

- `backend/app/audit.py` — Hash-chain audit_log writer (tamper-evident)
- `backend/app/observability_routes.py` — Audit + failed-jobs + stuck queries
- `backend/app/observability_metrics.py` — Prometheus custom metrics

**Rules & Compliance:**

- `backend/app/rules_routes.py` — Rule catalog browser + CRUD
- `backend/app/directives_routes.py` — Directive management (LOAs, compliance rules)
- `backend/app/glossaries/agents.json`, `suppliers.json` — Static business data

**RAG (Retrieval-Augmented Generation):**

- `backend/app/rag/search.py` — pgvector cosine similarity queries
- `backend/app/rag_routes.py` — RAG search endpoint for reviewer assistant
- `backend/app/agent/tools.py` — Tool implementations (search_rules, search_loas, etc.)

**Deal & Customer Management:**

- `backend/app/customers_routes.py` — Customer CRUD
- `backend/app/deals_routes.py` — Deal lifecycle + verdict rollups
- `backend/app/deal_lifecycle.py` — State machine (lead_gen → closer → etc.)

**Tracker (XLSX Import/Export):**

- `backend/app/tracker_routes.py` — Tracker board read endpoint
- `backend/app/tracker_edit_routes.py` — Inline edit endpoint (PATCH /api/tracker/rows/{id})
- `backend/app/import_xlsx_routes.py` — Bulk import endpoint
- `backend/app/import_xlsx_tracker.py` — XLSX parsing + model binding

## Naming Conventions

**Files:**

- Route handlers: `{feature}_routes.py` (e.g., `hitl_routes.py`, `agents_routes.py`)
- Service logic: `{domain}.py` (e.g., `compliance.py`, `tribunal_wer.py`, `rejection_factory.py`)
- Models: `models.py` (monolithic ORM)
- Utilities: `{purpose}.py` (e.g., `verification.py`, `resilience.py`, `audit.py`)
- Frontend pages: `page.tsx` in route directory (Next.js convention)
- Frontend components: `PascalCase.tsx` (e.g., `CallPreviewPanel.tsx`)

**Directories:**

- Feature modules: lowercase (e.g., `agent/`, `rag/`, `workflows/`, `storage/`)
- Components: `components/{category}/{name}.tsx` (e.g., `components/shared/CallPreviewPanel.tsx`)
- Migrations: `alembic/versions/{hash}_{description}.py`
- Routes: `frontend-v3/src/app/(group)/feature/` (route groups in parentheses)

**Database Tables:**

- Plural naming: `calls`, `call_checkpoints`, `customers`, `customer_deals`, `rules_chunks`, `directives`
- Soft deletes: not used (rows deleted or status changed)
- Timestamps: `created_at`, `updated_at` (UTC via `datetime.utcnow`)
- Foreign keys: `{table_singular}_id` (e.g., `call_id`, `customer_deal_id`)

**API Routes:**

- Resource endpoints: `/api/{resource}` (e.g., `/api/calls`, `/api/customers`)
- Action endpoints: `/api/{resource}/{id}/{action}` (e.g., `/api/calls/{id}/claim`)
- Webhook endpoints: `/api/inngest/*` (Inngest Cloud event delivery)
- Read-only: `GET /api/{resource}`, pagination via `limit` + `offset` (cursor in some cases)

## Where to Add New Code

**New Feature (HITL-like workflow):**
- Primary code: `backend/app/{feature}_routes.py` (HTTP handlers)
- Service layer: `backend/app/{feature}.py` (business logic)
- Models: Add columns + migration to `backend/alembic/versions/`
- Frontend: `frontend-v3/src/app/(admin)/{feature}/` or `(reviewer)/{feature}/`
- Tests: `backend/tests/test_{feature}.py`

**New Component/Module (e.g., extractor, detector):**
- Implementation: `backend/app/{category}/{module}.py` (e.g., `extraction/custom_detector.py`)
- If needs routing: Add route to `{feature}_routes.py`, include router in `main.py`
- If async + durable: Add Inngest function to `workflows/{module}.py`, register in `main.py:serve()`
- Tests: `backend/tests/test_{category}_{module}.py`

**New Tool (smart agent):**
- Definition: `backend/app/agent/tools.py` (add tool definition + handler)
- Handler logic: `backend/app/agent/tool_handlers.py` (or colocate in tools.py)
- Playbook update: `backend/app/agent/playbooks.py` (update system prompt for the tool)
- Tests: `backend/tests/test_agent_tools.py`

**Frontend Page (admin feature):**
- Route: Create `frontend-v3/src/app/(admin)/{feature}/page.tsx`
- Queries: Add to `frontend-v3/src/lib/queries/admin.ts` if missing
- Mutations: Add to `frontend-v3/src/lib/mutations/admin.ts` if needed
- Components: Colocate in same directory or extract to `components/`
- Tests: `frontend-v3/tests/{feature}.spec.tsx` (Playwright for E2E, Vitest for unit)

**New Utility (shared):**
- Shared backend: `backend/app/utils/{name}.py` (or add to existing file if small)
- Shared frontend: `frontend-v3/src/lib/{name}.ts`
- Consider creating a module file if utilities grow beyond 200 lines

## Special Directories

**backend/app/glossaries/:**
- Purpose: Static business data (agent names, supplier catalog)
- Generated: No (hand-maintained JSON)
- Committed: Yes
- Used by: Business detection, fuzzy matching

**backend/app/templates/:**
- Purpose: Jinja2 email templates
- Generated: No (hand-edited)
- Committed: Yes
- Used by: Email routes (confirmation emails, Wave-3.B)

**frontend-v3/.next/:**
- Purpose: Next.js build output (standalone bundle)
- Generated: Yes (npm run build)
- Committed: No (.gitignore)

**backend/__pycache__/, frontend-v3/node_modules/:**
- Purpose: Runtime dependencies + compiled Python bytecode
- Generated: Yes (Python import, npm install)
- Committed: No (.gitignore)

**docker-compose.observability.yml:**
- Purpose: Wave-2 observability stack overlay (optional)
- Generated: No (hand-maintained)
- Committed: Yes (reference, not activated by default)
- Activation: `docker-compose -f docker-compose.yml -f docker-compose.observability.yml up`

---

*Structure analysis: 2025-01-09*
