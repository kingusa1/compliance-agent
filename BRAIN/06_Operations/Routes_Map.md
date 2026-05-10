---
created: 2026-05-10
updated: 2026-05-10
tags: [ops, routes, api]
---

# Routes map

## Frontend routes (Next.js App Router 16)
**Base:** `https://compliance-agent-mu.vercel.app`

### Auth-free
- `/` → 307 → `/dashboard`
- `/login` — Supabase auth
- `/totally-bad-path` (any unknown route) → branded `not-found.tsx` (200 with custom UI)

### `(admin)` group — admin/lead access
- `/dashboard` — KPI strip + Quick Start + Quick Action tiles
- `/calls` — flat call list (with **trash icon delete on hover**)
- `/calls/[id]` — call detail with **PipelineTimeline** + checkpoint cards
- `/customers` — customer rollup
- `/customers/[slug]` — customer detail with **N-stage workflow bar** + tooltip
- `/deals` — deal list
- `/scripts` — supplier scripts catalog
- `/scripts/[id]` — script detail
- `/agents` — agent list
- `/agents/[name]` — agent drilldown (Recent flags / Open directives / Dead rejections / Similar failures)
- `/tracker` — full Watt-XLSX tracker (5 tabs: awaiting_review / active / fixed / dead / compliant)
- `/rejections` — rejections master-detail
- `/compliant` — compliant calls
- `/non-compliant` — non-compliant calls
- `/observability` — pipeline runs, stuck calls, audit log
- `/guide` — comprehensive user manual (sticky ToC)
- `/settings` — admin settings

### `(reviewer)` group — reviewer access
- `/queue` — review queue with claim flow
- `/calls/[id]` — same as admin route, mounted in reviewer layout

## Backend routes (FastAPI)
**Base:** `https://compliance-agent-production-690e.up.railway.app`

### Public / health
- `GET /api/health` — `{"status":"healthy"}`
- `GET /healthz` — Railway healthcheck `{"status":"ok"}`
- `GET /readyz` — DB readiness `{"status":"ready","checks":{"db":"ok"}}`
- `GET /api/log` (POST) — frontend log proxy

### Calls
- `GET /api/calls?limit=N` — list (no auth)
- `GET /api/calls/{call_id}` — full detail
- `GET /api/calls/{call_id}/script-checkpoints` — checkpoint definitions
- `POST /api/calls/upload` — multipart upload
- `POST /api/calls/{call_id}/retry` — re-run pipeline (preserves transcript, drops checkpoints)
- `POST /api/calls/{call_id}/checkpoint/{cp_index}/retry` — single-checkpoint re-analysis
- `POST /api/calls/{call_id}/reanalyze` — Inngest replay (analyze→score→finalize)
- `DELETE /api/calls/{call_id}` — full delete (drops file + checkpoints)
- `POST /api/calls/cleanup` — admin sweep
- `POST /api/admin/quality-resolve` — Quality Agent across all completed calls

### Customers / deals
- `GET /api/customers`
- `GET /api/customers/{slug}` — detail with embedded deals
- `GET /api/customers/{slug}/timeline` — call/deal timeline
- `GET /api/customers/{slug}/rollup` — aggregated stats
- `GET /api/deals`
- `POST /api/deals/stub` — create stub deal for upload

### Scripts
- `GET /api/scripts` — list
- (CRUD via `script_routes.py`)

### Agents
- `GET /api/agents` — list
- `GET /api/agents/{name}/drilldown` — drilldown stats
- `PATCH /api/agents/{name}` — set retraining flag

### Rejections
- `GET /api/rejections?tab=active|fixed|dead|archive` (auth-protected)
- `GET /api/rejections/{id}`
- `PATCH /api/rejections/{id}` — update status / category / fix_required

### Tracker
- `GET /api/tracker/rows?tab=...` (auth-protected — bearer JWT)

### Stats / observability
- `GET /api/stats` — dashboard KPIs
- `GET /api/observability/runs`
- `GET /api/observability/metrics` (404 currently — frontend may use different path)

### Auth-only / HITL
- `POST /api/calls/{call_id}/claim` — reviewer claim
- `POST /api/calls/{call_id}/release-idle`
- `POST /api/calls/{call_id}/verdict` — reviewer override

### Internal
- Inngest endpoint at `/inngest` (gated on `INNGEST_SIGNING_KEY` env presence)
