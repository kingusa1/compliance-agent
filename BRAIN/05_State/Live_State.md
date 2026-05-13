---
created: 2026-05-10
updated: 2026-05-12
tags: [state, live, ground-truth, mid-rebuild]
---

# Live State — mid-rebuild 2026-05-12 (taxonomy + per-segment pipeline)

> ⚠️ **MID-REBUILD AS OF 2026-05-12.** Backend Phases 0-4 of the
> taxonomy-rebuild + content-classifier work are on disk locally but
> **UNCOMMITTED** (except Phase 0 which is pushed as `818e312`).
> Frontend still speaks the OLD vocabulary (call_type radio, 5-bucket
> pills, HelpBanners). Phase 5 frontend overhaul not started.
>
> User explicitly said **no pushes without approval**. Phase 0 wipe
> endpoint is live but **NOT YET RUN** — will run right before Phase 7
> smoke test.
>
> Plan file (approved): `C:\Users\kingu\.claude\plans\magical-booping-crown.md`
> Resume guide: [[../04_Sessions/2026-05-12_Session_taxonomy_rebuild]]
>
> Earlier 2026-05-11: shipped color-coded 3-vs-4 stage `WorkflowTypePill`
> on `/customers`, `/customers/[slug]`, `/calls/[id]`. Pill is auto-derived
> from the AI-detected supplier label — emerald `3-stage · LOA bundled`
> for E.ON variants, blue `4-stage · separate LOA` for everyone else.
> Aly ask drafted at `comms/2026-05-11_Aly_ask.md` (4 blockers consolidated).
> Playwright-verified on prod (`dpl_HzAFRTJoxPuBi4T96V3jLLqKDQQt`).
>
> Earlier 2026-05-10 late: 5 bugs + 5 UX fixes shipped after a full
> Playwright-driven sweep. Live test login created; ground-truth upload
> validated (Bonnie Clarke = first 3/3 compliant call in DB).
>
> See [[../04_Sessions/2026-05-11_Session_workflow_pill]] for the full punch list.

> Single source of truth on what's deployed and verified. Update after every deploy.

## Frontend (Vercel)
- **Alias:** `compliance-agent-mu.vercel.app`
- **Current deployment:** Railway backend on commit `1e7e90c` (sync reanalyze-all endpoint + LLM script-checkpoint extractor + Option A multi-file same-deal default + `.planning/phase2-docs` bundled in Dockerfile)
- **Previous Vercel:** `dpl_F5oVygMyiRMaCPFjxZoDxyJmG574` (workflow pill + AI call_type + admin backfill)
- **Project rootDirectory:** `frontend-v3` ✓
- **Project framework:** `nextjs` ✓
- **Auto-deploy:** **NOT wired** — `link.deployHooks: []` on the Vercel project. Pushes to `main` do not trigger Vercel. Trigger via API POST `v13/deployments` with `gitSource={type:github,repoId:1233382040,ref:main,sha:<HEAD>}`.
- **17 routes HTTP-status verified 200/307** (desktop+mobile, 36 runs): `/` (307→/dashboard), `/login`, `/dashboard`, `/queue`, `/calls`, `/tracker`, `/customers`, `/customers/<slug>`, `/deals`, `/rejections`, `/scripts`, `/agents`, `/agents/Parat`, `/compliant`, `/non-compliant`, `/observability`, `/guide`, `/settings`
- ⚠️ **Content NOT verified:** for an unauthenticated visitor every protected route renders the **Sign In** form, not the page content. The HTTP code is 200 because Next.js renders the layout shell first then the auth guard hijacks. **Future visual audits need a working test login on prod Supabase.** See `audit-2026-05-10/AUDIT_REPORT.md` and `audit-2026-05-10/shots/dashboard_desktop.png`.
- **Branded 404:** `/some-bad-path` returns the `not-found.tsx` page (contains "ComplianceAI" header + quick-links). NOT the raw Vercel `bom1::xxx` page.

## Backend (Railway)
- **URL:** `https://compliance-agent-production-690e.up.railway.app`
- **Healthcheck:** `/healthz` → 200, `/api/health` → 200, `/readyz` → 200 (`db: ok`)
- **Service:** `compliance-agent` on project `compliance-agent-backend`
- **Latest commit deployed:** `c087493` (frontend type fix); backend latest `4e77515` (auto Quality Agent)

## Database state (post 2026-05-11 deep-clean session)
- **Calls:** 37 total · 19 with `call_type` correctly classified (lead_gen/passover/closer/standalone_loa/c_call/amendment), 15 still `full` (legacy uploads, no filename hint — verify correctly anyway under "full = lead_gen+passover+closer" rule for E.ON)
- **Customers:** 18 (down from 22; manual consolidation of 3 Little Farm variants + 2 Hanif + 2 Corner Cuts + "The Coach" + name renames Jill/John/Peter → full names)
- **Deals:** 18 — every deal lifecycle recomputed
- **Compliant:** 8 calls
- **Non-compliant:** 28 calls
- **Compliance rate:** 21.6%
- **Deal lifecycle distribution:** 12 verified · 2 passover_done · 1 closer_done · 1 lead_gen_done · 1 amendment_done · 1 c_call_done · 0 open
- Earlier states: see [[../04_Sessions/2026-05-10_Session_audit_late]] and [[../04_Sessions/2026-05-11_Session_workflow_clarity]]

## Test login (admin)
- Email: `admin@compliance-agent.local`
- Password: `Audit-Pass-2026-05-10!`
- Reset via Supabase admin API at `PUT /auth/v1/admin/users/<id>`

## (legacy snapshot below — pre-audit-late)

## Database state (post 2026-05-10 audit)
- **Customers:** 5 visible
  - `dorothy's evangelical church` — 3 calls, 1 deal, suppliers `[E.ON Next]` (Quality Agent merge result)
  - `crosby garage` — 1 call, 1 deal, suppliers `[E.ON Next]`
  - `korner kutz (audit upload)` — 1 call, 1 deal, suppliers `[E.ON Next]` (added 2026-05-10 audit)
  - `(auto-detect pending 42a89a59)` — **0 calls** (call was deleted), 1 orphan deal stub (delete endpoint doesn't cascade up)
  - `(pending audio upload)` — 0 calls, 1 stub deal
- **Calls:** 5 total — all `completed`. Failed `42a89a59` was deleted in the audit. Audit's own `190868a8-…` could NOT be deleted (HTTP 500 — see Known_Issues "DELETE on completed calls").
- **Deals:** 5 total
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
- `44f0201` — fix(ux): always-visible delete + reason column + script-text fallback + remove claim flow
- `4d3ae1a` — docs(brain): create Obsidian vault
- `c087493` — fix: Th component empty children TypeScript error
- `786e5e5` — feat(ux): trash-icon delete on calls list
- `4e77515` — feat(agents): auto-run Quality AI Agent on every upload
- `9d2f458` — feat(agents): Quality AI Agent (Opus 4.7) — cross-call identity resolution
- `d8e2502` — fix(pipeline): bidirectional human-name match + cross-deal supplier inheritance
- `c5bca2f` — fix(pipeline): human-name stitch searches Call.customer_name
- `5e48f70` — fix(pipeline): allow stitch on retries

## What shipped 2026-05-10 (evening — fixes pass)

Backend (Railway, deployed via GitHub auto-deploy on push to `main`):
- `CallSummary.reason` field added → /non-compliant table now shows AI reason instead of "—"
- `/api/calls/{id}/script-checkpoints` falls back to V1 TPI rules when matched script has empty `checkpoints` (which is true for ALL 15 seeded scripts) — stops `(Script text unavailable …)` empty state

Frontend (Vercel, deployed via API trigger to `prj_eHIyIFyxusNdCd6mR9Ff469NrcKO`, deploy id `dpl_tqUvcoWHP5toL9p9TMRGCiC7qPjv`):
- `/calls` trash icon always visible (was hidden behind `group-hover:visible`)
- Claim/Unclaim workflow removed from UI:
  - `/queue` filter chips simplified to All / Pending / Reviewed (was: All / Unclaimed / In review / Reviewed today)
  - `/queue` CTA changed from "Claim & review" to plain "Open & review" link
  - `useClaimCall` hook no longer imported by any UI (kept in lib for legacy)
  - `CallPreviewPanel` (used by /non-compliant rail) — status pill collapses unclaimed + in_review to "Pending"
  - `QueueDetailPanel` — same pill simplification + Open & review CTA
  - Dashboard description updated

## Known limits (not bugs)
See [[05_State/Known_Issues]].

## Test data
See [[05_State/Test_Calls]].
