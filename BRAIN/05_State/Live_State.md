---
created: 2026-05-10
updated: 2026-05-10
tags: [state, live, ground-truth]
---

# Live State — verified 2026-05-10 (most recent)

> Single source of truth on what's deployed and verified. Update after every deploy.

## Frontend (Vercel)
- **Alias:** `compliance-agent-mu.vercel.app`
- **Current deployment:** `compliance-agent-lbmlgbzj9-mohamed-hishams-projects-0b4feda9.vercel.app`
- **Project rootDirectory:** `frontend-v3` ✓
- **Project framework:** `nextjs` ✓
- **17 routes verified 200/307:** `/` (307→/dashboard), `/login`, `/dashboard`, `/queue`, `/calls`, `/tracker`, `/customers`, `/customers/<slug>`, `/deals`, `/rejections`, `/scripts`, `/agents`, `/agents/Parat`, `/compliant`, `/non-compliant`, `/observability`, `/guide`, `/settings`
- **Branded 404:** `/some-bad-path` returns the `not-found.tsx` page (contains "ComplianceAI" header + quick-links). NOT the raw Vercel `bom1::xxx` page.

## Backend (Railway)
- **URL:** `https://compliance-agent-production-690e.up.railway.app`
- **Healthcheck:** `/healthz` → 200, `/api/health` → 200, `/readyz` → 200 (`db: ok`)
- **Service:** `compliance-agent` on project `compliance-agent-backend`
- **Latest commit deployed:** `c087493` (frontend type fix); backend latest `4e77515` (auto Quality Agent)

## Database state
- **Customers:** 4 visible
  - `dorothy's evangelical church` — 3 calls, 1 deal, suppliers `[E.ON Next]` (Quality Agent merge result)
  - `crosby garage` — 1 call, 1 deal, suppliers `[E.ON Next]`
  - `(auto-detect pending 42a89a59)` — 1 failed call, 1 deal (stub never resolved because pipeline failed)
  - `(pending audio upload)` — 0 calls, 1 stub deal
- **Calls:** 5 total (4 completed, 1 failed)
- **Deals:** 4 total
- **Scripts:** 15 active (E.ON × 5, Scottish Power × 3, BG × 2, BGL × 2, EDF × 2, Pozitive × 1)

## Auto-running agents
- **Quality Agent** auto-runs on every upload via `pipeline._step_finalize → auto_resolve_for_call`
- Per-checkpoint analyzer always runs in `_step_analyze_checkpoints`
- Vulnerability detector runs in `_step_finalize`
- Pricing-mismatch flags run in `_step_finalize` when feature flag is on

## Env keys set (Railway)
- `OPENROUTER_API_KEY` ✓ (anthropic/claude-opus-4.7)
- `OPENROUTER_MODEL=anthropic/claude-opus-4.7` ✓
- `DEEPGRAM_API_KEY` ✓
- `DEEPGRAM_BASE_URL=https://api.eu.deepgram.com` ✓
- `DEEPGRAM_LANGUAGE=en-GB` ✓
- `DATABASE_URL` ✓ (Supabase pooler)
- `SUPABASE_URL` ✓
- `INNGEST_SIGNING_KEY` ✓
- `INNGEST_EVENT_KEY` ✓
- `INNGEST_ENV=production` ✓
- `USE_INNGEST_PIPELINE=false` ← intentionally; asyncio path is the live one

## Recent commits (most-recent first)
- `c087493` — fix: Th component empty children TypeScript error
- `786e5e5` — feat(ux): trash-icon delete on calls list
- `4e77515` — feat(agents): auto-run Quality AI Agent on every upload
- `9d2f458` — feat(agents): Quality AI Agent (Opus 4.7) — cross-call identity resolution
- `d8e2502` — fix(pipeline): bidirectional human-name match + cross-deal supplier inheritance
- `c5bca2f` — fix(pipeline): human-name stitch searches Call.customer_name
- `5e48f70` — fix(pipeline): allow stitch on retries

## Known limits (not bugs)
See [[05_State/Known_Issues]].

## Test data
See [[05_State/Test_Calls]].
