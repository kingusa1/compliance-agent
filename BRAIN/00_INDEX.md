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

1. [[05_State/Live_State]] — what's deployed right now
2. [[05_State/Known_Issues]] — open gaps and limits
3. [[04_Sessions/2026-05-10_Session]] — most recent change log
4. [[07_Tomorrow/Next_Steps]] — what's queued next

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

### 03 AI Pipeline
- [[03_AI_Pipeline/Pipeline_Stages]] — the 5-stage flow on every upload
- [[03_AI_Pipeline/Speaker_Detection]] — signal-based agent vs customer
- [[03_AI_Pipeline/Quality_Agent]] — cross-call identity resolver (the headline feature)
- [[03_AI_Pipeline/Future_Agents]] — roadmap of multi-agent expansion

### 04 Sessions
- [[04_Sessions/2026-05-10_Session]] — morning: Quality Agent + Brain bootstrap
- [[04_Sessions/2026-05-10_Session_audit]] — evening: end-to-end audit (Playwright + API)
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
