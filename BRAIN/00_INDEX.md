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

**As of 2026-05-12: MID-REBUILD. Read these in order before doing ANYTHING:**

1. [[04_Sessions/2026-05-12_Session_taxonomy_rebuild]] — **CURRENT STATE.** Backend Phases 0-4 done on disk but uncommitted; Phase 5 frontend NOT started; user wants NO PUSHES without approval. Has full resume guide + commit commands.
2. The plan file: `C:\Users\kingu\.claude\plans\magical-booping-crown.md` (user-approved).
3. [[05_State/Live_State]] — what's actually deployed vs what's pending.
4. [[02_Domain/Stage_Terminology]] — Aly's nomenclature locked to 4 stages.
5. [[05_State/Known_Issues]] — open gaps and limits.
6. [[04_Sessions/Decisions]] — running architectural decisions log.

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
- [[04_Sessions/2026-05-12_Session_taxonomy_rebuild]] — **⚠️ MID-REBUILD AT COMPACTION.** Locked call_type to `{lead_gen, pre_sales, verbal, loa}`; built content_classifier agent (1-4 segments per recording); per-segment pipeline + score aggregator; rejection auto-create disabled. Backend Phases 0-4 on disk uncommitted (Phase 0 pushed as `818e312`). Phase 5 frontend overhaul not started. Plan at `~/.claude/plans/magical-booping-crown.md`. **Resume guide inside the session file.**
- [[04_Sessions/Decisions]] — running list of architectural decisions

### 05 State
- [[05_State/Live_State]] — what's deployed and verified working RIGHT NOW
- [[05_State/Test_Calls]] — every test call uploaded + its current verdict
- [[05_State/Known_Issues]] — open gaps + their workarounds

### 06 Operations
- [[06_Operations/Deploy_Commands]] — copy/paste deploy cheat sheet
- [[06_Operations/Routes_Map]] — frontend + backend route inventory
- [[06_Operations/Credentials]] — where each secret/key lives (NOT the keys)

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
