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

**As of 2026-05-16 (late): HUMAN-REVIEW PIPELINE IS COSMETIC — VERDICT SUBMIT IS A PROTOTYPE. P0 FIX RUN IN PROGRESS.**
Read in order:

1. [[04_Sessions/2026-05-16_Session_queue_human_review_audit_verification]] — **READ FIRST.** Forensic verification of two external audits (96-step Queue audit + Playwright pipeline-walk audit). Headline: `VerdictTab.handleSubmit` is a prototype that `console.log`s + toasts "(prototype — payload logged)" but never calls `POST /api/calls/{id}/verdict`. Single defect cascades to: Reviewed tab stuck at 0, `/rejections` Active permanently empty, Compliant/Non-compliant pages show AI scores as if signed-off. Also: claim/release unwired, Tracker CATEGORY filters decorative, Edit-metadata corrupts customer names, Rejections sub-tabs infinite loading. **Fix sequence + audit corrections (10 of 29 audit claims are wrong/stale) inside.**

2. [[05_State/Live_State]] — **GROUND TRUTH ON DEPLOY.** Tip backend `3e57545`. (a) Reverted all Sonnet routing on detectors back to Opus 4.7 (Mohamed mandate: Sonnet's transcripts were unreliable on supplier / names / business / call_type). Both `openrouter_model` and `openrouter_cheap_model` now point at `anthropic/claude-opus-4.7` so any leftover `cheap=True` callsite still gets Opus. (b) Added trailing-tokens deal-linker shortcut: if last 2 non-stopword tokens of the business name match EXACTLY between target and candidate, drop fuzzy floor 0.80 → 0.40. Awais 4-call retest: 4 calls → **2 deals** (3 collapsed onto `6ac65bac 'Awais Mustafa Ta Charles Palace'`, one leadgen call still stub because BUSINESS_DETECT returned None on that transcript — transcript-limited). (c) Deleted duplicate Vercel project `compliance-agent-feat-wave5-deploy` that was auto-deploying-and-blocking on every push; only `compliance-agent` (`prj_eHIyIFyxusNdCd6mR9Ff469NrcKO`) remains.

2. [[04_Sessions/2026-05-16_Session_six_hour_run]] — Earlier 2026-05-16. Tip backend + frontend both `3ecd34c`. Shipped (a) `app/realtime.py` + `app/realtime_routes.py` SSE pub/sub fan-out — `GET /api/calls/events` (global) + `GET /api/calls/{id}/events` (per-call), backed by an in-memory asyncio.Queue keyed on call_id; pipeline `_trace_step` publishes 6 step transitions + step_started/ok/err, upload boundary publishes `queued`. Frontend `useCallEvents` hook + ScreenFrame layout mount; dropped 3s in-flight refetchInterval from `useCallDetailQuery`, `useCallCheckpointsQuery`, `useAdminCallsQuery` (60s safety-net poll remains). (b) Deal-linker Metaphone phonetic uplift in `_maybe_merge_into_existing_deal` (first-2-tokens phonetic-equal OR all-token Jaccard ≥ 0.5 → fuzzy floor 0.80 → 0.60) + `detect_business_name` routes to Opus 4.7 for non-EON suppliers. **Awais 4-call retest: STILL 4 deals** — root cause is upstream transcription drift ("Charles Palace" vs "Shah's Palace" vs empty BUSINESS_DETECT), not deal-linker logic. (c) Full Playwright sidebar audit (15 pages + 5 call-detail mutations). One bug found and fixed: `/queue` Reviewed tab was sending `filter=today`, backend pattern is `^(all|unclaimed|in_review|reviewed_today)$` → 422. Fixed at the wire boundary in `lib/api.getQueue`. **Phase 1 acceptance criteria ALL PASS**: audio reset bug FIXED (Play → 28.4s paused on second click, no reset), spacebar guard FIXED (53-char comment with spaces; audio playing throughout), L2_EXTRACTION_WRITE clean with no PendingRollbackError / no ck_flags_risk_tag.

2. [[04_Sessions/2026-05-16_Session_polling_rollback_and_handoff]] — Earlier 2026-05-16. Reverted the aggressive `refetchInterval` polling from `eb5566d` because it was re-mounting `<audio>` and resetting playback to 0 every cycle. Plus second-pass `_maybe_merge_into_existing_deal` invocation using `override_customer_name=business_name`. Plus `risk_tag=None` on vulnerability flags. Plus spacebar guard on Override→Fail textarea. Plus Sonnet 4.6 routing via `cheap=True` on cheap detectors. Tip backend + frontend both `e1c8d3b`.

2. [[04_Sessions/2026-05-15_Session_classifier_l2_realtime]] — Earlier overnight: 3 root-cause fixes (classifier `[]` on BG, L2 segment-write 6-stage crash, agent_name="Bounced" regression). Three bugs Lucca's British Gas uploads exposed: (a) content classifier returned `[]` on every non-E.ON call because the prompt was E.ON-flavoured — fixed with supplier-neutral signal language + `min_confidence` 0.5 → 0.35; (b) `_write_extraction_outputs` was crashing every call silently with `L2_EXTRACTION_FAILED` because it re-inserted segments using the obsolete 6-stage taxonomy that the DB CHECK constraint forbids — fixed by stopping the segment re-write entirely (classifier is now sole writer); (c) `agent_name="Bounced"` regression from `it's bounced back to me` — fixed by removing `it's/it is` from strict triggers + new gated `_IT_IS_AGENT_INTRO` regex + expanded stopword list. Plus full **real-time UI**: QueryProvider defaults flipped to `staleTime: 0` + `refetchInterval: 5_000` + window-focus refresh; queue/tracker/rejections poll at 3s, call detail at 1.5s while processing. DB wiped, 4 Clifton files uploaded — all 4 completed cleanly, 2 of 4 multi-segment (LOA → verbal+loa; Passover → pre_sales+verbal), agent names all real. Tip backend `0c2408e`, tip frontend `eb5566d`. Remaining concerns parked: supplier mis-detection on non-E.ON, customer-name = person-not-business, deal-linker doesn't collapse same-customer.
2. [[04_Sessions/2026-05-15_Session_vercel_unblock_and_revalidation]] — Diagnosed why every CLI Vercel deploy this evening was `BLOCKED` (`COMMIT_AUTHOR_REQUIRED`: `IT@bbmgroup.io` not a verified team seat — CLI-only block, GitHub-source bypasses it). Triggered GitHub-source deploy via REST API for sha `dc05258`; READY in 64 s; `compliance-agent-mu.vercel.app` auto-aliased. Playwright re-walked the Andrew call live: LOA `0% · 0/11 · Needs Review` (was `82% · 0/11 · Coaching`), Verbal `85% · 22/26 · Coaching` (was classifier `82%`), CP09/CP24 `NON-COMPLIANT · HUMAN` (was `Passed`). `/queue` / `/tracker` / `/rejections` all clean. Rejection-pipeline contract test on `bad39296`: lowercase "fail" accepted → 2 rejections created, all with `confirmed_by` populated → all visible in `/rejections?source=reviewer`. Test artifacts cleaned up.
2. [[04_Sessions/2026-05-15_Session_pipeline_validation]] — End-of-day session driven by user opening Andrew call (`2652a095`) and spotting `82% · 0/11 · Coaching` math contradiction + CP09/CP24 "Passed" badge while Human Review marked Fail. Playwright walk of the rejection pipeline uncovered TWO P0 contract bugs: `submit_verdict` case-insensitivity (lowercase `"fail"` silently bypassed auto-rejection branch) + `auto_create_rejection_for_verdict` left `confirmed_by=NULL` so reviewer-created rejections were hidden from `/rejections?source=reviewer`. Plus 5 supporting fixes: tracker N+1 (301→5 queries), pipeline normalize step (CP20 backfill), bucket gate (medium-only at <50% pass → review not coaching), pass-rate% UI swap, reviewer-override top badge. Backend ALL LIVE on Railway (`5708bcf` + earlier). Vercel queue was stuck — superseded by tonight's GitHub-source unblock (see above).
2. [[04_Sessions/2026-05-15_Session_deal_linker_tracker_filters]] — Earlier today: Shipped 4-tier match cascade at intake (MPAN/MPRN/DocuSign/Companies-House hard keys + cleanco+rapidfuzz+jellyfish probabilistic composite, calibrated thresholds 0.99 auto / 0.85 review), advanced tracker filter bar (date range, day, multi-supplier, multi-agent, status, verdict, deadline state, value range, MPAN search), and side-panel editable Identity + Meter&Deal + Deadline + Assignee cards. Backend `tracker_edit_routes.ALLOWED_FIELDS` split into REJECTION_FIELDS + DEAL_FIELDS so PATCH writes to the right table; new `POST /api/tracker/rows/{id}/assignee` and `GET /api/reviewers/active` endpoints. 17 new matcher unit tests pass. 3 commits pushed (`3b9bf0d` · `f8b1a0a` · `8b8f2e0`) and validated live via Playwright. Splink/F-S deferred — current weighted-sum gives the same calibrated band gates without DuckDB.
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
- [[06_Operations/Model_Routing]] — **WHICH LLM TIER GOES ON WHICH AGENT.** Read this when adding any new `_call_llm` callsite. Decision rubric + current production wiring + forbidden swaps (the 5 detectors that must stay on Opus 4.7).
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
