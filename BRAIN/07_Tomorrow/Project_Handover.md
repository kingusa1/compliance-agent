---
created: 2026-05-10
updated: 2026-05-10
tags: [demo, handover]
---

# Project handover — 2026-05-11

## Context
- This is **NOT a live demo** — it's a project handover for delivery.
- Audience: stakeholder(s) at the very-big-company that commissioned the system (referenced as "Watt Utilities" throughout).
- The screenshare flow below walks through the AI architecture and shows it working with real recorded calls.

## Pre-flight (5 min before)
1. Verify `compliance-agent-mu.vercel.app/dashboard` returns 200
2. Verify `compliance-agent-production-690e.up.railway.app/api/health` returns `{"status":"healthy"}`
3. Open both in browser tabs
4. Have `BRAIN/00_INDEX.md` open in a 2nd window for reference

## Demo flow

### Act 1 — System overview (3 min)
Open `/dashboard`:
- KPI strip (total calls, compliant, non-compliant, compliance rate)
- HelpBanner: "Upload a call → Deepgram → script match → Opus 4.7 → Queue/Compliant. Every step is auto-detected."
- Quick-action tiles linking to every page

### Act 2 — The AI Pipeline visible (3 min)
Open any call detail page (e.g. `/calls/<crosby-grange-id>`):
- **PipelineTimeline** at the top: 5 stages with status icons
  - Deepgram Nova-3 transcription
  - Speaker labels (Agent / Customer)
  - Supplier auto-detection
  - Script auto-match
  - Opus 4.7 checkpoint analysis
- Tooltip on each stage explains what it does
- Per-checkpoint cards below show evidence + AI reasoning notes

### Act 3 — The smoking-gun moment (5 min)
Open `/customers/dorothy's evangelical church`:
> "Three calls were uploaded for this customer. The per-call AI extracted three different business names: `The Church`, `Evangelical Church`, `St. Peter's Benfleet Church`. The system ALSO mis-tagged the broker `Afak` as the customer.
>
> Then we run the **Quality AI Agent** — Opus 4.7 reads all three transcripts together. Watch:"

Show the customer rollup:
- 1 customer: **Dorothy's Evangelical Church**
- 1 deal · 3 calls · supplier `E.ON Next`
- Confidence: 0.92
- Reason: *"All three calls reference Christopher Neil Banks, Evangelical Church, same postcode, E.ON Next contract with agent Afak."*

> "The system fixed itself. Per-call detection got us 80%; cross-call AI orchestration gets us the last 20%."

### Act 4 — The 2-vs-3 stage rule (2 min)
On the same customer page, point at the deal's **WorkflowBar**:
- "**2-stage workflow · E.ON Next**" with hover tooltip
- Tooltip: "E.ON Next bundles the LOA into the Closer call, so this deal needs 2 stages: Lead Gen → Closer."

> "Different suppliers have different lifecycle requirements. The system encodes Watt's compliance rule book — E.ON's 2 stages, every other supplier's 3 stages — and shows it natively in the UI."

### Act 5 — The reviewer flow (3 min)
Open `/queue`:
- Filters (Unclaimed / In review / Reviewed today)
- HelpBanner explaining: "AI flagged. You claim → accept or override. Your verdict is the audit-of-record."

Click into a queued call:
- Show how the reviewer can override the AI verdict
- Show how the override updates the audit log
- Show the "How to judge this" expander on each checkpoint

### Act 6 — Close (2 min)
Open `/guide`:
- Walk through the comprehensive user manual (sticky ToC)
- Show pipeline section, taxonomy section, lifecycle section, reviewer playbook
- "Anyone can train themselves on this system from this page."

## Key talking points
1. **No manual tagging.** Every supplier, agent, customer, script, call_type is auto-detected from the audio.
2. **AI's work is visible.** PipelineTimeline + per-checkpoint reasoning means reviewers don't trust a black box — they see what happened.
3. **Multi-agent today, more agents queued.** Quality Agent (Opus 4.7) shipped today merges sibling calls. Roadmap: Call-Type Classifier, Decision-Maker Confirmer, Verdict Reviewer, Data Enricher, Multi-Agent Orchestrator.
4. **Watt's rule book is the source of truth.** 8 Standards, 27 rejection codes, 6 suppliers, 15 scripts. All encoded in `backend/app/watt_compliance/`.
5. **Audit-of-record is the human.** AI gives a verdict; reviewer accepts or overrides; their decision is what gets archived.

## Backup plan if anything's broken
Re-alias to a known-good Vercel build using [[06_Operations/Deploy_Commands]]. If the backend is down: `railway up --service=compliance-agent --ci`. If the demo customer disappeared: `POST /api/admin/quality-resolve` to re-merge.

If asked technical questions:
- Architecture: see [[01_Project/Architecture]]
- Stack: see [[01_Project/Stack]]
- Pipeline: see [[03_AI_Pipeline/Pipeline_Stages]]
- Domain: see [[02_Domain/Watt_Compliance]] · [[02_Domain/Lifecycle]] · [[02_Domain/Suppliers]] · [[02_Domain/Scripts]]

## After the handover
- [[07_Tomorrow/Next_Steps]] — what comes after delivery
