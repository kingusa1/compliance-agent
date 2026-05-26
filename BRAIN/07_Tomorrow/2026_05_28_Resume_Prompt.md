---
created: 2026-05-27
updated: 2026-05-27
tags: [resume-prompt, next-session, frontend-bundle-hook, list-caching, qc-banner, d14-residual-lag]
---

# 2026-05-28 — Resume prompt for the next session

> Drop the content of the next code block into the next session as the initial user message. It triggers the full read-brain bootstrap, then continues with the carry-forward items left from 2026-05-27 PM.

---

## Copy-paste prompt (literal)

```text
/gsd continue from yesterday's session. Read the brain FIRST —
BRAIN/00_INDEX.md, BRAIN/05_State/Live_State.md, the most recent
04_Sessions/2026_05_27_Session_pm_perf_queue_agents_bundle.md (and
the morning one 2026_05_27_Session_full_day_agents_wave.md for
context), BRAIN/05_State/Known_Issues.md, and this file
(BRAIN/07_Tomorrow/2026_05_28_Resume_Prompt.md). Run the session-
start doctrine bootstrap (integrity verify, ledger list-active,
retro queue). After that:

PHASE 1 — Wire frontend useCallBundle hook (highest-leverage open item)
========================================================================
Backend ships /api/calls/{id}/bundle in commit 10522b8 returning detail
+ segments + words + script_checkpoints + audio_url in one response.
Frontend still fires 5 separate fetches on every call-detail page open.

Goal: cut call-detail page perceived load by 1.5-2 s.

Tasks:
1. New hook in frontend-v3/src/lib/queries/reviewer.ts:
   `useCallBundleQuery(id, callStatus?: string)` returning the bundle
   envelope. Query key: ["call", id, "bundle"]. Same status-conditional
   3s safety-net poll as the existing useCallDetailQuery.

2. In frontend-v3/src/app/(reviewer)/calls/[id]/page.tsx, replace the
   parallel useCallDetailQuery + useCallCheckpointsQuery +
   useCallWordsQuery + useCallSegments queries with ONE
   useCallBundleQuery. Keep useCallEvents(id) for SSE invalidation —
   the bundle key is `["call", id, "bundle"]` so the existing
   ["call", id] tree invalidation in useCallEvents handles it.

3. Derive the per-query selectors from the bundle:
   - `detail = bundle?.call`
   - `checkpoints = bundle?.script_checkpoints`
   - `segments = bundle?.segments`
   - `words = bundle?.words`
   - `audioUrl = bundle?.audio_url`

4. Keep the existing single-resource queries available as fallbacks if
   the bundle endpoint ever 404s (old backend).

5. Run code-reviewer on the changes (auto-trigger for
   frontend-v3/src/**/*.{ts,tsx}). Address CRIT+HIGH pre-push.

6. Measure: open a call detail page, time first-paint via Playwright.
   Expected: before 2.5s → after ~600ms.

PHASE 2 — Process-level cache for read-heavy list endpoints
=============================================================
/api/deals/list and /api/customers/list are visited often but the data
is stable for minutes at a time. Add an in-memory TTL cache keyed on
(query_params, reviewer_org_id) with 30-60s TTL.

Pattern: simple module-level dict with timestamp + TTL check, mirroring
app/profile_cache.py. Invalidate on:
- A new deal/customer is created (POST /api/deals/* or matcher upsert)
- A reviewer overrides a verdict on a call (the deal's composite_pct
  recomputes on next read)

python-reviewer + database-reviewer agents fire on backend changes;
address CRIT+HIGH pre-push.

PHASE 3 — QC banner UI on call detail page
===========================================
Backend already writes Call.quality_check JSONB envelope. Frontend
doesn't render it. Add a banner above the existing call-detail content:

- Verdict pill (ok/review/block) with the same tone palette as
  compliance_status (emerald/amber/red)
- Score percentage
- One-sentence summary
- Expandable issues[] list — each issue shows code, severity chip,
  field, expected vs got, evidence excerpt, fix_required

Component: frontend-v3/src/app/(reviewer)/calls/[id]/QualityCheckBanner.tsx.
Listen for SSE `quality_check_done` events via the existing
useCallEvents hook to repaint live.

PHASE 4 — D14 residual loop_lag (if time)
==========================================
`loop_lag_canary lag=1469ms` still fires during bulk-upload bursts.
Off-loop file reads helped 13s → 1.5s but didn't close the gap.
Profile sync paths in checkpoint_analyzer.py batch dispatch:
- json.loads / json.dumps on multi-KB LLM responses
- fuzzy_match Levenshtein code
Route through anyio.to_thread.run_sync. Measure with the existing
loop_lag_canary metric in main.py.

DOCTRINE
========
- Read BRAIN before any tool call. Cite paths.
- LAW_OF_SKILLS v2.1: declare the trio in TodoWrite items 1-3 BEFORE
  the first state-mutating tool call.
- LAW_OF_ENTERPRISE_GRADE: 12-line checklist on every fix.
- Push as kingusa1: `gh auth switch --user kingusa1` BEFORE every push.
- Auto-deploy: Railway picks up on push; Vercel needs the manual REST
  API curl (GitHub App still uninstalled).
- Run Session_Self_Audit before any "done" reply.

PROD ACCESS CHEAT SHEET
=======================
- Frontend: https://compliance-agent-mu.vercel.app
- Backend: https://compliance-agent-production-690e.up.railway.app
- Supabase token: localStorage `sb-zcmdsblqbgatsrofptsq-auth-token`
- Railway: RAILWAY_TOKEN=4ac76051-95e8-4979-9a5d-98656396d4f5
- Railway CLI on Windows: C:\Users\kingu\AppData\Roaming\npm\railway.cmd
- Vercel: token in ~/.secrets/vercel.env. Read with
  `awk -F= '/^VERCEL_TOKEN=/ {print $2}' ~/.secrets/vercel.env`
  (NOT grep+cut — that splits on the quotes inside the file and
  produces a 153-char malformed token that fails with HTTP 400).

LATEST TIP COMMITS
==================
10522b8 perf(routes): /api/calls/{id}/bundle composite endpoint
f65ee4e feat(agents): quality-reviewer dashboard upgrade
4204de4 fix(queue,verdict): per-checkpoint review_status auto-promote
a273e0b docs(brain): perf root-cause + auth profile-cache fix
69e79a8 perf(auth): wire profile_cache into current_user
e22f3c2 fix(routes,pipeline,alembic): D13 + D1/D2 + reviewer polish
dfcbb25 fix(verdict): code-reviewer HIGH+MED on name lookup
f5becf4 fix(pipeline,analysis): address python-reviewer CRIT+HIGH
f032114 feat(agents): transfer-aware + Pass-button name lookup + QC
3a84308 feat(verdict,d10): D10 n_a vocabulary + slow-button + realtime
```

---

## Why this prompt is structured this way

* **PHASE 1 first** because it's the highest-leverage open item and the backend is already in place — frontend wiring is mechanical.
* **PHASE 2** is enterprise-grade polish; cache the slow list endpoints to push the cross-region floor lower still.
* **PHASE 3** completes the QC agent loop — backend writes, frontend renders, owner-mandated.
* **PHASE 4** is the last open lag investigation; defer if PHASE 1-3 take longer than expected.

## Files to skim before starting

1. `BRAIN/00_INDEX.md` — vault map
2. `BRAIN/05_State/Live_State.md` — Wave 7 + Wave 8 (PM) blocks at the top
3. `BRAIN/04_Sessions/2026_05_27_Session_pm_perf_queue_agents_bundle.md` — yesterday PM full log
4. `BRAIN/04_Sessions/2026_05_27_Session_full_day_agents_wave.md` — morning context (D9, n_a, QC, Pass-button)
5. `BRAIN/05_State/Known_Issues.md` — D1-D14 register + new D-* shipped IDs
6. `backend/app/routes.py:get_call_bundle` (around line 2168) — the bundle endpoint contract
7. `frontend-v3/src/app/(reviewer)/calls/[id]/page.tsx` — the 5 separate queries to replace
