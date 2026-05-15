---
created: 2026-05-10
updated: 2026-05-10
tags: [index, brain]
---

# 🧠 Compliance Agent — Project Brain

**Owner:** Mohamed Hisham Ismail (kingusa1) for **Watt Utilities** compliance auditing
**Live URLs:**
- Frontend: https://compliance-agent-mu.vercel.app
- Backend: https://compliance-agent-production-690e.up.railway.app
- Supabase: project `zcmdsblqbgatsrofptsq` (ap-south-1)

> **Purpose of this Brain.** Every fact, decision, file path, gotcha, credential location, and current-state pointer lives here. When a Claude session restarts, read [[00_INDEX]] first, then [[05_State/Live_State]], then whatever's most relevant. **No re-discovery from scratch.**

---

## 🚨 Read FIRST when resuming a session

**As of 2026-05-15: BULLETPROOF DEAL-LINKER + ADVANCED TRACKER FILTERS + EDITABLE SIDE PANEL.**
Read in order:

1. [[04_Sessions/2026-05-15_Session_deal_linker_tracker_filters]] — **MOST RECENT.** Shipped 4-tier match cascade at intake (MPAN/MPRN/DocuSign/Companies-House hard keys + cleanco+rapidfuzz+jellyfish probabilistic composite, calibrated thresholds 0.99 auto / 0.85 review), advanced tracker filter bar (date range, day, multi-supplier, multi-agent, status, verdict, deadline state, value range, MPAN search), and side-panel editable Identity + Meter&Deal + Deadline + Assignee cards. Backend `tracker_edit_routes.ALLOWED_FIELDS` split into REJECTION_FIELDS + DEAL_FIELDS so PATCH writes to the right table; new `POST /api/tracker/rows/{id}/assignee` and `GET /api/reviewers/active` endpoints. 17 new matcher unit tests pass. 3 commits pushed (`3b9bf0d` · `f8b1a0a` · `8b8f2e0`) and validated live via Playwright. Splink/F-S deferred — current weighted-sum gives the same calibrated band gates without DuckDB.
2. [[04_Sessions/2026-05-15_Session_local_dev_audit]] — Earlier today: Five parallel reviewers found 25 bugs across tracker / deals / customers / rejections / queue / dashboard / agents / pipeline / Quality Agent / auth surface; 17 P0/P1 fixed locally. Local stack stood up (backend `:8001`, frontend `:3000`) on the DEV Supabase `fgkzmldgpfezyqzjuqfq` (549 calls, 152 rejections). Mid-session logout + relogin of GitHub/Vercel/Railway; new auth state recorded in [[../06_Operations/Credentials]]. Four commits (`becb958` · `1b55dec` · `30fa836` · `147dcd5`) all pushed.
2. [[04_Sessions/2026-05-14_Session_reviewer_polish]] — Yesterday: eight reviewer-facing bugs shipped — real speaker names in transcript (`Afak / AGENT / 0:00`); LOA router resilient fallback against `script_name~'LOA'` when `lifecycle_phase` is NULL; CheckpointCard 2-row header; `/api/calls/{id}/script-checkpoints` returns UNION of every segment's script; drag-to-scrub on the audio bar; Chat tab gated behind "Coming soon"; deterministic regex layer in `detect_names` catches unusual transliterated names. Tip `8eb9763` (already on origin/main).
2. [[04_Sessions/2026-05-14_Session_phase5_complete]] — earlier today: shipped all 9 Phase 5 sub-tasks (a-i) + 4 intelligence endpoints + per-segment cards on call detail. Found+fixed the 7th CI-red test (non_compliant_v2 — same severity-weighted fix pattern as partial_v2). Fixed a runtime SQLAlchemy bug in intelligence_routes (`func.cast` → `case-when`). All CI green. Vercel + Railway both deployed.
2. [[04_Sessions/2026-05-13_Session_deploy_and_19of19]] — Yesterday's deploy of the taxonomy rebuild, the silent alembic-chain failure since 2026-05-06 that we unblocked, the 4-pass extractor that filled the last 3 prose-heavy scripts, and the /scripts UI upload rewire.
2. [[05_State/Live_State]] — current commit tip (`394c438`), deploy URLs, DB state (0 calls — wiped), Script counts (19/19), full recent-commit chain.
3. [[04_Sessions/2026-05-12_Session_taxonomy_rebuild]] — yesterday's design + Phase 0-4 backend work (uncommitted then, all shipped now).
4. The plan file: `C:\Users\kingu\.claude\plans\magical-booping-crown.md` (user-approved).
5. [[02_Domain/Stage_Terminology]] — Aly's nomenclature locked to 4 stages.
6. [[05_State/Known_Issues]] — open gaps (6 CI tests + Phase 5 UI a-i).
7. [[04_Sessions/Decisions]] — running architectural decisions log.

---

## 🗂 Vault map

### 01 Project
- [[01_Project/Overview]] — what this system actually does
- [[01_Project/Architecture]] — backend/frontend/AI layers
- [[01_Project/Stack]] — tech inventory
- [[01_Project/Deploy]] — Vercel + Railway + Supabase + Inngest

### 02 Domain (the business problem)
- [[02_Domain/Watt_Compliance]] — 8 Standards + 27 rejection codes
- [[02_Domain/Suppliers]] — 6 suppliers, the alias map, the canonical labels
- [[02_Domain/Scripts]] — 15 supplier scripts + the 2-vs-3-stage rule
- [[02_Domain/Lifecycle]] — Lead Gen → Closer → Standalone LOA
- [[02_Domain/Stage_Terminology]] — Aly's "Opener/Closer{Pre-Sales,Verbal,LOA}" vs doc terminology vs system routing (2026-05-12)

### 03 AI Pipeline
- [[03_AI_Pipeline/Pipeline_Stages]] — the 5-stage flow on every upload
- [[03_AI_Pipeline/Speaker_Detection]] — signal-based agent vs customer
- [[03_AI_Pipeline/Quality_Agent]] — cross-call identity resolver (the headline feature)
- [[03_AI_Pipeline/Future_Agents]] — roadmap of multi-agent expansion
- [[03_AI_Pipeline/Tracker_Autofill_Plan]] — 3 new specialist agents to AI-fill every tracker column (Date / Rejection-Advisor / Deadline)

### 04 Sessions
- [[04_Sessions/2026-05-10_Session]] — morning: Quality Agent + Brain bootstrap
- [[04_Sessions/2026-05-10_Session_audit]] — afternoon: end-to-end audit pass
- [[04_Sessions/2026-05-10_Session_evening_sweep]] — evening: agents + dashboard + dedup + delete-cascade
- [[04_Sessions/2026-05-10_Session_audit_late]] — late: Playwright sweep, 5 bugs + 5 UX, first compliant call (Bonnie Clarke)
- [[04_Sessions/2026-05-11_Session_overnight]] — autonomous 5h run: 7 commits, 27 calls, 7 compliant, 26% rate, deal lifecycle wired end-to-end
- [[04_Sessions/2026-05-11_Session_workflow_clarity]] — corrected supplier-stage matrix from 2/3 to **3/4** (added Passover), new dedicated `/workflow` page, customer-detail crash fix, rogue Vercel project deleted
- [[04_Sessions/2026-05-11_Session_workflow_pill]] — color-coded `WorkflowTypePill` on /customers, /customers/[slug], /calls/[id]; AI-detected supplier drives `3-stage · LOA bundled` (emerald) vs `4-stage · separate LOA` (blue); Aly ask consolidated to 4 blockers
- [[04_Sessions/2026-05-11_Session_ai_call_type]] — `detect_call_type` AI classifier replaces filename pre-pass; transcript AGENT/CUSTOMER role tagging; upload-modal click fix; /guide rewritten with 15-step pipeline + AI classifier rules; 15 historical calls backfilled (11 deals re-lifed)
- [[04_Sessions/2026-05-11_Session_deep_audit]] — root-cause audit: every script had `checkpoints=[]` so every call was graded on 3 universal rules. Built LLM script-checkpoint extractor + admin ingest endpoint → **164 checkpoints written across 10 of 15 scripts** (E.ON Next NHH+HH now has 26 rules used by 73% of calls). Built sync `reanalyze-all` endpoint (Inngest path was a no-op in prod). After reanalyze: scores moved from N/3 cluster → N/26 with `f017bb03 → 22/26`
- [[04_Sessions/2026-05-12_Session_taxonomy_rebuild]] — Locked call_type to `{lead_gen, pre_sales, verbal, loa}`; built content_classifier agent (1-4 segments per recording); per-segment pipeline + score aggregator; rejection auto-create disabled. Designed-on-disk state (now all deployed — see 2026-05-13 session). Plan at `~/.claude/plans/magical-booping-crown.md`.
- [[04_Sessions/2026-05-13_Session_deploy_and_19of19]] — **DEPLOY DAY.** Shipped Phases 0-4 + Phase 5j to prod (`compliance-agent-mu.vercel.app` + Railway `394c438`). Unblocked a silent alembic-chain failure that had been broken since 2026-05-06 (`failed_jobs` DuplicateTable was killing every migration after it). Added Call.segments + Call.flags relationships to fix 500-on-upload. Built 4-pass script extractor (strict → prose-mode → per-page split → deterministic heuristic) so **all 19 of 19** supplier scripts now have non-empty checkpoints. Rewired `/scripts` UI upload to use the same extractor. **Resume guide inside the session file.**
- [[04_Sessions/2026-05-14_Session_phase5_complete]] — Phase 5 a-i UI overhaul + 4 intelligence endpoints + per-segment cards + CI fix (7th red test) + intelligence_routes `func.cast` → `case-when` runtime bug.
- [[04_Sessions/2026-05-14_Session_reviewer_polish]] — Eight reviewer-facing bugs shipped: real speaker names in transcript · LOA router fallback (script_name match) · CheckpointCard 2-row header · script-checkpoints UNION across segments · drag-to-scrub audio bar · Chat tab "Coming soon" · bulletproof agent-name regex layer + backfill endpoint.
- [[04_Sessions/2026-05-15_Session_local_dev_audit]] — **MOST RECENT.** System-wide audit sweep via 5 parallel reviewers found 25 bugs, 17 fixed locally across tracker, deals, customers, rejections, queue, dashboard, agents, pipeline V1 fallback, Quality Agent ordering, AI narrative provenance, and 5 auth gaps. Local dev stack stood up (backend `:8001`, frontend `:3000`) against the DEV Supabase `fgkzmldgpfezyqzjuqfq` (549 calls, 152 rejections) because prod Railway dropped to HTTP 000 from this shell mid-session. 4 unpushed commits awaiting go-ahead.
- [[04_Sessions/Decisions]] — running list of architectural decisions

### 05 State
- [[05_State/Live_State]] — what's deployed and verified working RIGHT NOW
- [[05_State/Test_Calls]] — every test call uploaded + its current verdict
- [[05_State/Known_Issues]] — open gaps + their workarounds
- [[05_State/Scripts_Validation_2026_05_15]] — /scripts page audit vs source docs: 15/16 supplier scripts + 4/5 phrase packs ingested; Valda + verbal_confirmation pack missing

### 06 Operations
- [[06_Operations/Deploy_Commands]] — copy/paste deploy cheat sheet
- [[06_Operations/Routes_Map]] — frontend + backend route inventory
- [[06_Operations/Credentials]] — where each secret/key lives (NOT the keys)
- [[06_Operations/Available_Skills]] — full Claude Code skills roster (~1500 entries) with auto-trigger conditions, grouped by domain. **Read this when wondering "what can the assistant do without me asking?"**
- [[06_Operations/Skill_Routing]] — **task pattern → skill matrix**. Maps user intents (plan / bug fix / refactor / SQL change / etc.) to the exact skills the assistant should auto-fire, in parallel where possible. Loaded into context via the project-root `CLAUDE.md` so the right skills fire without the user typing the slash name. **Update after every multi-skill task** — closed loop between observed behaviour and next-session behaviour.

### 07 Tomorrow
- [[07_Tomorrow/Project_Handover]] — the live demo script
- [[07_Tomorrow/Next_Steps]] — queued multi-agent work

---

## 📜 Brain protocol — for the next Claude session

When the user says **"read brain"** or **"read obsidian"**:
1. Open [[00_INDEX]] (this file)
2. Open [[05_State/Live_State]] for ground truth on production
3. Open the most recent file in `04_Sessions/` for what we just did
4. Don't re-discover; cite the file path when answering

When the user says **"update brain"** or after any significant change:
1. Append a dated entry to today's `04_Sessions/<date>_Session.md`
2. If state changed: update [[05_State/Live_State]]
3. If a decision was made: append to [[04_Sessions/Decisions]]
4. If a new gotcha emerged: add to [[05_State/Known_Issues]]
5. Commit the BRAIN/ folder so Obsidian sync (or git pull) picks it up

When you (Claude) finish work in any session:
- ALWAYS update the day's session file before answering "done" to the user.
- ALWAYS update [[05_State/Live_State]] if a deploy / data change happened.
- That's the price of admission for "session continuity".
