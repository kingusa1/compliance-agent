---
created: 2026-05-12
tags: [session, taxonomy, rebuild, content-classifier, per-segment, client-feedback]
---

# Session 2026-05-12 — Taxonomy Rebuild + Client Feedback Sweep

> **Plan file:** `C:\Users\kingu\.claude\plans\magical-booping-crown.md`
> (approved by user; ExitPlanMode confirmed).

## Where this session stands at compaction time

**Backend Phases 0-4 — DONE locally on disk. Phase 0 alone pushed (`818e312`). Phases 1-4 are UNCOMMITTED edits. User explicitly said "don't push without my approval." Resume by reviewing the uncommitted diff and committing (no push) before any further code work.**

| Phase | Status | Notes |
|---|---|---|
| 0 Wipe endpoint | ✅ pushed `818e312` | Endpoint live on Railway, **NOT yet run.** Run with `POST /api/admin/wipe-all-calls?confirm=YES_DELETE_EVERYTHING` right before Phase 7 smoke test. |
| 1 Taxonomy lockdown | ✅ on disk, uncommitted | call_type vocab now `{lead_gen, pre_sales, verbal, loa}` only. Migration `2026_05_12_lock_call_type_taxonomy.py` (rev `4f9c1d27_locktax`). |
| 2 Content classifier | ✅ on disk, uncommitted | `backend/app/agents/content_classifier.py` — Opus 4.7 returns 1-4 segments. |
| 3 Per-segment pipeline | ✅ on disk, uncommitted | New `_step_classify_content`; `_step_analyze_checkpoints` loops over CallSegment rows; `_step_score` aggregates worst-bucket-wins. Migration `2026_05_12_callsegment_verdict_cols.py` (rev `7a9d4e1f_segvrd`). CallSegment extended with score/bucket/compliant cols; CallCheckpoint.segment_id added. |
| 4 Rejection gating | ✅ on disk, uncommitted | `_maybe_create_rejection` removed from `process_call`. `/api/rejections` defaults `source=reviewer` filter; customer_name joined. |
| 5 Frontend overhaul (a-j) | ⏳ NOT STARTED | 10 sub-items below. |
| 6 Multi-call lifecycle | ✅ baked into Phase 1 | `derive_lifecycle_status` uses `_phase_done_for` (latest-call-per-phase wins). |
| 7 Local tests + deploy + wipe + smoke | ⏳ pending |  |
| 8 BRAIN final update | ⏳ in progress (this file) |  |

## Aly's stage model (confirmed 2026-05-12)

Reviewer drops ONE recording. AI auto-detects which segments are inside. Segments:

| Segment | Rule set | Notes |
|---|---|---|
| `lead_gen` | 88-rule phrase pack | First contact, qualification |
| `pre_sales` | Same 88-rule phrase pack | Warm-up at start of closer (different content, same rules) |
| `verbal` | Supplier verbal-contract script (E.ON NHH+HH 26, BG Acq 21, etc.) | Legally binding contract reading |
| `loa` | Supplier LOA script (E.ON only — 11 cps) | LOA wording. **Non-E.ON LOAs are always paper/DocuSign, never audio.** Classifier drops `loa` segments emitted for non-E.ON suppliers. |

Each segment → routed via `rubric_router.route_for_segment(segment_type, call, db)` → graded by `analyze_all_checkpoints` against its rubric → severity-weighted bucket (`pass / coaching / review / blocked`).

Call-level verdict = worst-bucket across all segments. Score = `Σpassed / Σtotal` across segments.

## Latest-call-per-phase rule

`deal_lifecycle._phase_done_for(calls, phase)`:
- Find all calls of `call_type == phase`
- Sort by `created_at` asc
- The **latest** one's `compliance_status == 'compliant'` (or legacy `compliant == True`) determines whether the phase is done
- If `lead_gen #1` failed and `lead_gen #2` is compliant → phase done
- If `lead_gen #1` compliant then `lead_gen #2` failed → phase NOT done

## Confirmed product decisions (asked via AskUserQuestion)

| Decision | Answer |
|---|---|
| Email send button | **Keep stub, label "Coming soon"** — disable visually, code parked. |
| Multi-call deal compliance | **Latest call per phase wins.** |
| Dashboard Intelligence v1 cards | **All four:** Compliance % by supplier, Top-10 agents %, Calls by call_type, 30-day trend. |
| Zero-segment classifier output | **Halt with `needs_classification` status** — reviewer manually triages. |

## What's still pending (Phase 5)

Frontend overhaul covers these client-PDF feedback items:

### 5a — Review Queue (`src/app/(reviewer)/queue/page.tsx`)
- Add customer_name column (currently missing)
- Add call_type / segment-list column (currently shows "—")
- Replace generic "Pending" pill with **"AI: 22/26 ⚠"** + **"To Review"** double-pill
- Hide rows with score=0% (route them to a separate "Processing" tab)
- Drop the "AI" badge — column header is enough

### 5b — Call Detail (`src/app/(reviewer)/calls/[id]/`)
- Restore top-row filter chips (Passed N · Partial N · Non-Compliant N — click to filter checkpoint list)
- 1-click pass on CheckpointCard (no comment prompt); reject still opens comment modal
- TranscriptTimeline must render clear AGENT/CUSTOMER labels (role tagging fix already in DB, ensure UI uses it loudly)
- Drop per-checkpoint "needs_review" yellow pill — only Pass / Partial / Non-Compliant
- Verdict top row: 3 pills only (**Pass / Non-Compliant / Needs Review**) — hide Coaching + Block
- Risk tags: conditional render (only when verdict ∈ {Needs Review, Non-Compliant})
- Dismiss pending-actions when all checkpoints have a reviewer verdict
- Email button: disabled with "Coming soon" tooltip
- NEW component `SegmentCards.tsx`: one card per CallSegment with own score / bucket / checkpoint list

### 5c — Tracker (`src/app/(admin)/tracker/page.tsx`)
- Auto-refresh on queue verdict-submit (`queryClient.invalidateQueries`)
- Better filters: date range, supplier multi-select, call_type multi-select, score range, customer search
- Drop "AI" labels from row rendering

### 5d — Rejections (`src/app/(admin)/rejections/page.tsx`)
- Already gated server-side (Phase 4 set `source=reviewer` default)
- Show customer_name column (API already returns it)
- Update help banner copy

### 5e — Agents (`src/app/(admin)/agents/page.tsx`)
- Switch from absolute integer counts to **percentages** (compliance rate %)

### 5f — Dashboard Intelligence panel (`src/app/(admin)/dashboard/page.tsx`)
- New `IntelligencePanel.tsx` with 4 cards:
  1. Compliance % by supplier (bar)
  2. Top-10 agents by compliance % (table)
  3. Calls by call_type (donut: lead_gen/pre_sales/verbal/loa)
  4. 30-day compliance trend (line, weekly buckets)
- New backend `intelligence_routes.py` with `/api/intelligence/by-supplier|by-agent|by-call-type|trend`

### 5g — Observability — remove from sidebar
- Delete entry at `src/components/Sidebar.tsx:71`

### 5h — HelpBanner cleanup
- Remove from 6 pages: Queue, Tracker, Rejections, Dashboard, Customers, Deals

### 5i — All-calls module
- Verify `/calls` exists and surfaces all calls; ensure sidebar link

### 5j — Drop call_type radio from upload modal
- `src/components/intake/L7Form.tsx` — remove call_type selector; help text: "AI auto-detects segments"

## Critical files modified locally (uncommitted)

```
backend/app/analysis.py
backend/app/deal_lifecycle.py
backend/app/agents/rubric_router.py
backend/app/agents/content_classifier.py            (NEW)
backend/app/pipeline.py
backend/app/models.py                                (CallSegment extended + CallCheckpoint.segment_id)
backend/app/rejections_routes.py
backend/alembic/versions/2026_05_12_lock_call_type_taxonomy.py     (NEW)
backend/alembic/versions/2026_05_12_callsegment_verdict_cols.py    (NEW)
```

## How to resume after compaction

1. **Read this file** (you're here) + the plan at `C:\Users\kingu\.claude\plans\magical-booping-crown.md`.

2. **Verify uncommitted backend state**:
   ```
   cd "c:/Users/kingu/Downloads/projects/complinace project/compliance-agent-feat-wave5-deploy/compliance-agent-feat-wave5-deploy"
   git status
   ```
   Expect: 7 modified backend files + 3 new files (content_classifier.py + 2 migrations).

3. **Sanity-check the backend imports** (the venv has all deps; system Python doesn't):
   ```
   cd backend && ./venv/Scripts/python.exe -c "
   from app.pipeline import _step_classify_content, _step_analyze_checkpoints, _step_score
   from app.agents.content_classifier import classify_content
   from app.agents.rubric_router import route_for_segment
   from app.deal_lifecycle import derive_lifecycle_status, required_phases
   print('imports ok')
   "
   ```

4. **Commit the backend phases as 4 separate commits** (no push yet — user must approve push):
   ```
   git add backend/app/analysis.py backend/app/deal_lifecycle.py backend/app/agents/rubric_router.py backend/alembic/versions/2026_05_12_lock_call_type_taxonomy.py
   git commit -m "feat(backend): Phase 1 — lock call_type taxonomy + latest-wins lifecycle"

   git add backend/app/agents/content_classifier.py
   git commit -m "feat(ai): Phase 2 — content_classifier agent (1-4 segments per recording)"

   git add backend/app/pipeline.py backend/app/models.py backend/alembic/versions/2026_05_12_callsegment_verdict_cols.py
   git commit -m "feat(pipeline): Phase 3 — per-segment _step_classify_content + analyzer loop + score aggregator"

   git add backend/app/rejections_routes.py
   git commit -m "feat(rejections): Phase 4 — reviewer-only filter + customer_name join"
   ```

5. **Continue with Phase 5 frontend** (10 sub-items above) — recommend doing them in chunks (5a-5e first, then 5f-5j) per the user's request earlier in the session.

6. **Before any deploy** run locally:
   - `cd backend && ./venv/Scripts/python.exe -m pytest tests/ -k "lifecycle"` — verify the new latest-wins rule
   - `cd frontend-v3 && npx tsc --noEmit` — confirm no type drift
   - `cd frontend-v3 && NEXT_TELEMETRY_DISABLED=1 npx next build` — production build green

7. **Get explicit "go" from user** before:
   - `git push origin main`
   - Triggering Railway redeploy
   - Triggering Vercel deploy
   - Running `POST /api/admin/wipe-all-calls?confirm=YES_DELETE_EVERYTHING` on prod

## Things the user told me explicitly

- "Don't push without my approval" — must wait for explicit go.
- "Finish everything locally then run it locally and check" — local pytest + tsc + next build before any push request.
- "Remove all 37 calls from the database" — Phase 0 wipe endpoint ships; will run after Phase 7.
- "Show only lead_gen, pre_sales, verbal, loa anywhere in the system" — Phase 1 enforces backend; Phase 5j enforces frontend (drop the call_type radio).
- "AI must decide which segments are inside automatically" — Phase 2 + 3 implement this; the reviewer never picks a call_type at upload.

## Open Aly clarifications still in `comms/2026-05-11_Aly_ask.md`

Not blocking the rebuild but flagged for next time we talk to Aly:
- Q1: E.On parent vs E.On Next — same supplier or split? (Currently treated as the same E.ON variant.)
- Q3: 5 unparseable scripts need plain `1. 2. 3.` numbering (BGL V7, BG Acq, BG Renewal, EDF V11, Pozitive). Their `Script.checkpoints` are still empty; until reformatted, those suppliers' verbal segments fall through to V1.
- Q4: Sample audio for non-E.ON closes (we have lots of E.ON test audio; nothing for BG/BGL/EDF/SP/Pozitive).

## What lives in BRAIN that's relevant

- `02_Domain/Stage_Terminology.md` — Aly's nomenclature vs docs vs system. Just shipped this session (commit `d9d7d1b`).
- `02_Domain/Lifecycle.md` — needs update post-Phase 1 to reflect the new SUPPLIER_PHASE_MATRIX (all suppliers now `[lead_gen, pre_sales, verbal]`).
- `04_Sessions/2026-05-11_Session_ai_call_type.md` — prior session that built the supplier-script checkpoint extractor + phrase-pack extractor; those still apply.
- `04_Sessions/2026-05-11_Session_workflow_pill.md` — the workflow type pill is still live; needs label tweaks in Phase 5 (Closer → Verbal, Passover → Pre-Sales).

## Prod state when this session started

- DB has 37 calls (will be wiped Phase 7).
- 10 of 15 Script rows have checkpoints (164 total — from yesterday's ingest).
- 4 of 5 phrase packs ingested: lead_gen 88, passover 88, c_call 32, amendment 32 = 240 rules. **Note: post Phase 0 wipe, the phrase pack `lifecycle_phase='passover'` row stays in DB and is now reachable as `pre_sales` via the rubric router's `_PHRASE_PACK_PHASE` map. Both `lead_gen` and `pre_sales` segments route to `lifecycle_phase='lead_gen'` pack. The `passover` pack row is therefore orphaned — should be deleted in BRAIN follow-up or by an admin tool, NOT critical.**
- verbal_confirmation pack failed to ingest (Railway 5-min proxy timeout). Not blocking — verbal segments use supplier scripts.
- Vercel HEAD: `dpl_52sqJXFqqi6N1i7rLrpRWbRxLAt7` on commit `04afa71` (click-to-open file picker fix).
- Railway: auto-deploys on push to `main`; last push was `818e312` (wipe endpoint).

## Phrase pack relabel — open task

The `Script` row with `supplier_name='PHRASE_PACK'` and `lifecycle_phase='passover'` (88 rules) is technically orphaned now. The rubric router maps both `lead_gen` and `pre_sales` to `lifecycle_phase='lead_gen'`. Two options for Phase 7 cleanup:

(a) Delete the `passover` row + rename it to `pre_sales` — but the lookup goes via `lead_gen` so this isn't needed for correctness.

(b) Leave it as a no-op (route doesn't reach it). Cleaner DB but not blocking.

I'll do (b) — easier.

## TL;DR for compacted future-me

1. **Phase 0-4 backend code is DONE on disk, uncommitted.** Commit (no push) first thing.
2. **Phase 5 frontend is NOT started.** 10 sub-items; do in chunks (5a-e then 5f-j).
3. **Don't push or deploy or wipe without explicit user "go".**
4. **The plan file is the source of truth.**
