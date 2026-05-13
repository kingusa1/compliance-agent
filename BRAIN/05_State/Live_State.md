---
created: 2026-05-10
updated: 2026-05-14
tags: [state, live, ground-truth, phase-5-complete]
---

# Live State — Phase 5 complete + CI green 2026-05-14

> ✅ **Full Phase 5 (a-j) UI overhaul + 4 intelligence endpoints DEPLOYED 2026-05-14.**
> Tip commit `8ccef2b` (intelligence SQL fix), prior tips: `2801fb0`
> (Phase 5 a-i UI + intelligence + SegmentCards), `5de5820`
> (non_compliant_call_v2 test fix — first GREEN CI in 3 pushes),
> `1ae31ee` (6 tests fixed + pipeline excerpt + checkpoint_results),
> `3f222d4` (BRAIN). All 19/19 supplier scripts filled. CI test +
> coverage both GREEN. Frontend `next build` passes locally.
>
> Vercel: `dpl_B5i1YNKkrcJptkiAt8hTL7b59XUz` (commit `2801fb0`).
> Backend (Railway): tip `8ccef2b`. Both healthy.
>
> Reviewer-facing surface is now reduced to 3 verdict buttons
> (Pass / Needs Review / Non-Compliant); coaching + block buckets stay
> server-side. Risk tags only render on non-pass verdicts. AGENT /
> CUSTOMER labels are loud. 1-click pass commits immediately. New
> Intelligence panel on /dashboard shows compliance % by supplier,
> top-10 agents, calls by call_type donut, and 30-day trend. New
> SegmentCards stack on /calls/[id] surfaces per-segment verdicts.
>
> ✅ **2026-05-13 — Backend Phases 0-4 + Phase 5j (upload-boundary fix) DEPLOYED.**
> Tip commit `ddfdb23` (Call.segments + Call.flags relationships fix
> the 500 on upload-response serialization). Prior tips: `796fb62`
> (per-script commit in ingest endpoint), `a0c2da0` (V1 fallback +
> script_id-override + degradation status), `2100fdd` (classifier
> fallback for short transcripts + tests), `8423b64` (Phase 5j route
> + L7Form), `2a2f311` (BRAIN docs).
>
> The AI now auto-classifies recordings into 1-4 segments (lead_gen /
> pre_sales / verbal / loa); each segment grades against its own
> rubric; worst-bucket-wins aggregator emits a single call-level
> verdict. V1 fallback kicks in when no supplier rubric matches.
>
> Vercel: `dpl_29rNSwpsZPQog9JPtymCXETT2VXR` (commit `2100fdd`),
> aliased to `compliance-agent-mu.vercel.app`. (Tip Vercel deploy
> trails by 2 commits — fine since the frontend changes were only in
> 8423b64; later commits are backend-only fixes.)
>
> Phase 0 wipe ran. Supplier-script checkpoints re-ingested via the
> hardened prose-mode extractor: **16/19 Script rows filled (84%)**.
> Three still empty (EDF V11, Pozitive PE, Scottish Power TPI Acq) —
> calls on those suppliers fall through to V1 3-rule TPI fallback
> until reformatting + re-ingest.
>
> User opted out of the full Phase 5 frontend overhaul for now — the
> minimum Phase 5j change to L7Form + intake schema + upload route
> shipped so a live test upload works end-to-end against the new
> backend. Fuller UI overhaul (intelligence dashboard, segment cards,
> double-pill verdicts, agent percentages, HelpBanner removal) is
> queued.
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
- **Current Vercel deploy:** `dpl_29rNSwpsZPQog9JPtymCXETT2VXR` on commit `2100fdd` (Phase 5j L7Form fix). Subsequent backend-only fixes did not require a Vercel re-deploy.
- **Project rootDirectory:** `frontend-v3` ✓
- **Project framework:** `nextjs` ✓
- **Auto-deploy:** **NOT wired** — `link.deployHooks: []` on the Vercel project. Pushes to `main` do not trigger Vercel. Trigger via API POST `v13/deployments` with `gitSource={type:github,repoId:1233382040,ref:main,sha:<HEAD>}`. CLI token at `$APPDATA/com.vercel.cli/Data/auth.json`.
- **All routes 200/307** (verified 2026-05-13): root redirect, login, dashboard, queue, calls, tracker, customers, customers/<slug>, deals, rejections, scripts, agents, compliant, non-compliant, observability, guide, settings.
- ⚠️ **Auth-gate caveat (unchanged):** anonymous GET on protected routes renders the Sign-In form, not the page content. Use the test login below to see real pages.

## Backend (Railway)
- **URL:** `https://compliance-agent-production-690e.up.railway.app`
- **Healthcheck:** `/healthz` → 200, `/api/health` → 200, `/readyz` → 200 (`db: ok`)
- **Service:** `compliance-agent` on project `compliance-agent-backend`
- **Tip commit deployed (2026-05-13):** `394c438` — 4-pass extractor + heuristic fallback + /scripts upload rewire.
- **Recent chain (most recent first):**
  - `394c438` feat(ai): 4-pass extractor with deterministic heuristic fallback (19/19 scripts)
  - `b72f0c2` fix(migration): 3 more migrations idempotent (verdict_state, fix_narrative, pipeline_step_log)
  - `b9bc0a6` fix(migration): failed_jobs CREATE TABLE idempotent — **this unblocked the alembic chain that had been silently failing since 2026-05-06**
  - `ddfdb23` fix(models): add Call.segments + Call.flags relationships (500 on upload)
  - `796fb62` fix(admin): ingest-script-checkpoints commits per-script
  - `a0c2da0` fix(pipeline): segment-loop honours explicit script_id + degradation status
  - `2100fdd` fix(pipeline,rejections,tests): unblock CI after taxonomy rebuild
  - `8423b64` feat(intake): Phase 5j — drop stale call_type defaults at the upload boundary
  - `2a2f311` docs(brain): 2026-05-12 taxonomy rebuild — session log + Live_State + INDEX
  - `986be16` feat(ai): harden script_checkpoint_extractor for prose-heavy supplier scripts
  - `2f67c0d` feat(rejections): Phase 4 — reviewer-initiated only + customer_name join
  - `560edc9` feat(pipeline): Phase 3 — per-segment classify→analyze→aggregate flow
  - `9a71e16` feat(ai): Phase 2 — content_classifier agent emits 1-4 segments per recording
  - `3e1846b` feat(backend): Phase 1 — lock call_type taxonomy to {lead_gen,pre_sales,verbal,loa}
  - `818e312` feat(admin): POST /api/admin/wipe-all-calls (Phase 0 of taxonomy rebuild)
- **Railway CLI auth status:** logged in as `mohamedhisham735@gmail.com`; service `compliance-agent`. `railway logs --json` works for runtime + `railway logs --build --json` for builds.

## Database state (post 2026-05-13 wipe + re-ingest)
- **Calls:** 0 (Phase 0 wipe ran successfully on `2026-05-13T18:08` UTC; second wipe at `18:48` after smoke).
- **Customers:** 0 (cascade).
- **Deals:** 0 (cascade).
- **Rejections:** 0.
- **Scripts: 19 of 19 filled** ✅ (was 16/19 mid-rebuild). Counts:
  - PHRASE_PACK × 4: lead_gen 88, passover-as-handover 88, c-call 32, amendment 32
  - E.ON × 5: NHH+HH 26, Gas TPI 25, Gas (undated) 25, Elec 24, TPI Verbal LOA 11
  - British Gas × 2: Broker Acq 21, Broker Renewal 20
  - BGL × 2: Broker Acquisition V7 29, Acquisition (legacy) 30
  - Scottish Power × 3: Acquisition (TPI) 29, Renewal 28, Multisite 31
  - EDF × 2: TPI Fixed-for-Business V11 72, Pre-amble 12
  - Pozitive × 1: Verbal Contract (PE) 71
- **All Alembic migrations applied:** head reached (incl. `4f9c1d27_locktax` Phase 1 CHECK constraint + `7a9d4e1f_segvrd` Phase 3 segment columns + `call_checkpoints.segment_id` FK).

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
