# Technology Stack

**Analysis Date:** 2026-05-08

## Languages

**Primary:**
- Python 3.12 - Backend API (FastAPI), pipeline logic, LLM orchestration, audio processing
- TypeScript 5 - Frontend application, API client generation, type safety
- JavaScript - Browser runtime (React 19)

**Secondary:**
- SQL - Postgres database, migrations (Alembic)
- YAML - Infrastructure configuration (Docker Compose, CI/CD)

## Runtime

**Backend Environment:**
- Python 3.12 (via `Dockerfile FROM python:3.12-slim`)
- Uvicorn 0.30.0 ASGI server, 2 workers

**Frontend Environment:**
- Node.js (Next.js 16.2.4, React 19)
- Next.js development server, build server, static production server

**Package Managers:**
- pip (Python, backend)
- npm (Node.js, frontend)
- Lockfiles: `requirements.txt` (pinned), `package-lock.json` (implied)

## Frameworks

**Backend:**
- FastAPI 0.115.0 - REST API framework, async request handling
- SQLAlchemy 2.0.35 - ORM for Postgres, session management
- Alembic 1.14.0 - Database schema migrations
- Uvicorn 0.30.0 - ASGI web server
- Inngest 0.5-1.0 - Durable workflow orchestration engine
- pytest 8.3.0 + pytest-asyncio 0.24.0 - Testing framework
- pytest-cov 5.0.0 - Coverage reporting

**Frontend:**
- Next.js 16.2.4 - React metaframework, SSR, static generation
- React 19.2.4 - UI component library
- TanStack React Query 5.100.7 - Server state management, caching
- TanStack React Table 8.21.3 - Data grid/table abstraction
- React Hook Form 7.74.0 - Form state management
- Zustand 5.0.12 - Client state management
- Zod 4.4.1 - Schema validation, runtime type checking

**Frontend Testing:**
- Vitest 4.1.5 - Unit/integration test runner
- Playwright 1.59.1 - E2E testing framework
- @testing-library/react 16.3.2 - Component test utilities
- @playwright/test 1.59.1 - Playwright test runner

**Frontend Build & Tooling:**
- Tailwind CSS 4 - Utility-first CSS
- PostCSS 4 - CSS processing pipeline
- Prettier 3.8.3 - Code formatting
- ESLint 9.39.4 - Linting (extends Next.js config)
- TypeScript 5 - Strict type checking
- OpenAPI Typescript 7.13.0 - Generate API types from OpenAPI spec

**Frontend UI Components:**
- shadcn/ui 4.6.0 - Accessible component library (built on Radix + Tailwind)
- Radix UI 1.4.3 - Headless component primitives
- Base UI React 1.4.1 - Unstyled component hooks
- Lucide React 1.14.0 - Icon library
- Sonner 2.0.7 - Toast notifications
- Class Variance Authority 0.7.1 - CSS-in-JS variant utilities

**Dev Dependencies (Linting/Analysis):**
- Husky 9.1.7 - Git hooks
- Lint-staged 16.4.0 - Stage linting
- Axe DevTools (@axe-core/cli, @axe-core/playwright) - Accessibility audit
- Lighthouse 13.2.0 - Performance/PWA audit
- jsdom 29.1.1 - DOM simulation for unit tests

## Key Dependencies

**Critical Backend:**
- pydantic 2.10+ - Data validation, config management
- pydantic-settings 2.5+ - Environment config parsing
- asyncpg 0.30.0 - Async Postgres driver
- psycopg2-binary 2.9.10 - Sync Postgres driver (Alembic)
- httpx 0.27.0 - Async HTTP client (LLM + transcription API calls)
- tenacity 8.2.3 - Retry logic, exponential backoff

**Audio & STT:**
- deepgram-sdk 3.7.0 - Deepgram speech-to-text SDK
- PyPDF2 3.0.1 - PDF text extraction
- python-docx 1.1.2 - Word document parsing
- openpyxl 3.1+ - Excel import/export (.xlsx)

**Storage & Vector Search:**
- supabase 2.5+ - Supabase Python client (Postgres + Auth + Storage)
- pgvector 0.3+ - Vector embedding storage extension
- boto3 1.34.144 - AWS S3 / MinIO client
- moto[s3] 5.0.18 - S3 mocking for tests

**Authentication & Security:**
- pyjwt[crypto] 2.8+ - JWT verification with JWKS support (Supabase)
- python-json-logger 2.0.7 - Structured logging

**Observability:**
- sentry-sdk[fastapi] 2.18.0 - Error tracking (GlitchTip compatible)
- prometheus-fastapi-instrumentator 7.0.2 - Prometheus metrics export
- prometheus-client - Prometheus metric registry

**Async & Streaming:**
- sse-starlette 2.1.0 - Server-sent events (streaming responses)
- python-multipart 0.0.12 - Multipart form data parsing

**Development & Utilities:**
- python-dotenv 1.0+ - Load .env files (diagnostic scripts only)

**Frontend Dependencies (Key):**
- @supabase/supabase-js 2.105.1 - Supabase auth + real-time client
- @sentry/nextjs 10.51.0 - Frontend error tracking
- @hookform/resolvers 5.2.2 - Form validation resolvers (Zod, etc.)

## Configuration

**Backend Configuration:**
- Location: `backend/app/config.py`
- Method: Pydantic BaseSettings with .env file loading
- Key variables: All env-driven via Settings class
  - `DEEPGRAM_API_KEY` - Deepgram STT (optional, empty disables)
  - `OPENROUTER_API_KEY`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` - LLM providers
  - `ACTIVE_PROVIDER` - Which LLM provider to use (default: openrouter)
  - `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` - Supabase
  - `DATABASE_URL` - Postgres connection (SQLAlchemy DSN)
  - `MIGRATION_DATABASE_URL` - Session-mode pooler for Alembic
  - `SENTRY_DSN` - GlitchTip error tracking endpoint
  - `STORAGE_BACKEND` - "supabase" or "s3" (default: supabase)
  - `S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET` - MinIO or AWS
  - `SPEECHMATICS_API_KEY`, `ASSEMBLYAI_API_KEY` - Additional STT providers
  - `ADMIN_KEY` - Admin authentication override
  - `USE_AGENT_ANALYZER` - Feature flag for new agent analyzer
  - `USE_INNGEST_PIPELINE` - Feature flag for durable workflow (default: false)
  - Additional feature flags: `PRICING_MISMATCH_ENABLED`, `VULNERABLE_DETECTION_ENABLED`

**Database Configuration:**
- Postgres 16 (from docker-compose.observability.yml)
- Connection pooling: pool_size=10, max_overflow=20, pool_recycle=1800s
- Keepalives enabled with 30s idle timeout
- Migrations: `backend/alembic/` with Alembic CLI
- ORM: SQLAlchemy with declarative models in `backend/app/models.py`

**Frontend Configuration:**
- tsconfig.json: Strict mode, ES2017 target, path alias `@/*` → `src/*`
- next.config.js: (implicit, Next.js 16 defaults)
- vitest.config.ts: Unit test configuration
- playwright.config.ts: E2E test configuration
- prettier.config.js: (implicit or via .prettierrc)
- tailwind.config.js/ts: (implicit Tailwind 4)

## Build & Deployment

**Backend Build:**
- Docker image: `python:3.12-slim` base
- Entrypoint: `uvicorn app.main:app --host 0.0.0.0 --port 8001 --workers 2`
- Target deployment: Railway (managed container platform)
- Dockerfile: `backend/Dockerfile` (13 lines, straightforward setup)

**Frontend Build:**
- Build command: `next build`
- Development: `next dev`
- Production: `next start`
- Target deployment: Vercel (Next.js native)

**Development Tools:**
- Linting: ESLint 9.39.4 (extends `eslint-config-next`)
- Formatting: Prettier 3.8.3
- Type checking: `tsc --noEmit` (TypeScript compiler)
- Local dev database: Postgres 16 (via docker-compose.yml implied)

## Testing Infrastructure

**Backend:**
- Framework: pytest 8.3.0 + pytest-asyncio 0.24.0
- Coverage tool: pytest-cov 5.0.0
- Mocking: moto[s3] for S3 tests
- Run: `pytest` (from requirements.txt)

**Frontend:**
- Unit tests: `vitest run` or `vitest` (watch mode)
- E2E tests: `playwright test`
- Test coverage: Implicit support via Vitest
- Watch mode: `test:watch` script

## CI/CD

**Git Hooks:**
- Tool: Husky 9.1.7
- Pre-commit: Lint-staged 16.4.0 (stage linting)

**Observability Stack (Self-Hosted):**
- Error tracking: GlitchTip 4.1 (Sentry-compatible)
  - Backed by: Postgres 16, Redis 7
  - Ports: 8080 (tunneled, not public)
- Metrics: Prometheus (internal, port 9090)
- Logs: Loki (internal, port 3100)
- Visualization: Grafana (operator UI, port 3001)
- Composition: `docker-compose.observability.yml` (optional, runs alongside main stack)

## Platform Requirements

**Development:**
- Python 3.12 runtime
- Node.js (version not specified, assume recent LTS)
- Docker + Docker Compose (for local observability stack)
- Postgres 16 local instance (or Supabase-hosted)

**Production (Vercel + Railway):**
- Vercel: Next.js native environment, automatic deployments from git
- Railway: Containerized Python backend, Postgres datastore, environment variables
- Storage: Supabase Storage or S3-compatible (MinIO, AWS, Cloudflare R2)

---

*Stack analysis: 2026-05-08*
