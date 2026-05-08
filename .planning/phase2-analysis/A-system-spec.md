# A — System Spec Analysis

**Source docs:**
- `phase2-docs/compliance_xai__watt_ai_compliance_system.md` (system def — SD)
- `phase2-docs/compliance_xai__watt_ai_compliance_tech_spec.md` (tech spec — TS)
- `phase2-docs/compliance_xai__compliance_manual.md` (compliance manual — CM)

**Cross-reference:**
- `.planning/codebase/SUMMARY.md` (codebase summary — CS)
- `.planning/codebase/ARCHITECTURE.md` (architecture doc — AR)

---

## 1. What This System Does

The Watt Utilities AI Compliance Transcribe & Flagging System is an internal call-analysis tool that receives recordings of energy-broker sales calls (pre-sales lead generation and verbal contract closings), automatically transcribes them, segments them into canonical call stages, applies a rule-based + NLP compliance engine against Ofgem TPI standards, scores each call from 0–100, generates flag lists and risk tags, issues real-time on-screen alerts to agents during live calls, and triggers automated deal-blocking or review/escalation actions — all surfaced to compliance reviewers and team leads via a reporting/feedback layer.

---

## 2. Inputs

| Input | Source | Detail |
|-------|--------|--------|
| Call audio | Agent upload or live stream feed | Lead-gen calls and verbal-contract closing calls (SD §1) |
| Compliance rule set | Internal rule definitions (JSON) | Per-stage rules with severity + action fields (TS §4) |
| Script PDFs / call scripts | Reference material for adherence check | Verbal contract script must be followed (SD §4) |
| Supplier metadata | Internal CRM / agent profiles | Agent name, supplier name, commission rate (TS §5 — Agents table) |
| Customer identity / authority | In-call detection | Decision-maker confirmation required (SD §3) |

- No mention of LOAs, MPAN/MPRN docs, or Letters of Authority as AI inputs (those appear in CM §7 as manual document checks only).
- No RAG / vector search over rule documents mentioned in the spec docs.

---

## 3. Outputs

| Output | Description | Spec cite |
|--------|-------------|-----------|
| Compliance Score (0–100) | Starts at 100; deductions per severity tier | SD §8, TS §6 |
| Flag List | Per-rule flags with severity and transcript snippet | SD §6, TS §5 Flags table |
| Risk Tags | Ombudsman Risk, Mis-selling Risk, Complaint Risk, Cancellation Risk | TS §9 |
| Action Verdict | PASS / REVIEW / COACHING / BLOCK | SD §6, TS §6 |
| Real-time agent alerts | On-screen popup + optional whisper audio | SD §7, TS §7 |
| Auto-generated feedback email | Per-call, sent after analysis | TS §8 |
| Automation triggers | Escalate agent if ≥3 criticals/week; assign retraining | TS §10 |
| Database row | Calls, Compliance_Results, Flags, Agents tables updated | TS §5 |

---

## 4. Pipeline Stages

```
Audio In → STT → Segmentation → Rules Engine → Scoring → Alert/Output
```

| Stage | Purpose | Spec cite |
|-------|---------|-----------|
| 1. Audio Input | Receive call recording | TS §2 component 1 |
| 2. Speech-to-Text | Transcribe audio to text | TS §2 component 2; SD §2 step 1 |
| 3. Call Segmentation | Split transcript into Introduction / Qualification / Pitch / Transfer-Passover / Verbal Contract / Close | TS §3; SD §2 step 2 |
| 4. NLP Processing | Detect intent + flag phrases; not just exact-match | TS §2 component 3; SD §5 |
| 5. Compliance Rules Engine | Apply per-stage rule set (JSON rules with condition / severity / action) | TS §2 component 4; TS §4 |
| 6. Scoring Engine | Derive 0–100 score; map to Pass/Review/Coaching/Fail | TS §2 component 5; TS §6; SD §8 |
| 7. Alert Engine (real-time) | Emit on-screen popup / whisper audio to agent during live call | TS §2 component 6; TS §7; SD §7 |
| 8. Output Engine | Produce report + auto-email; write DB rows | TS §2 component 7–8; TS §8 |
| 9. Automation Triggers | Block deal / escalate agent / assign retraining based on thresholds | TS §10 |

---

## 5. Roles

| Role | Interaction point | Spec cite |
|------|------------------|-----------|
| Sales Agent | Receives real-time alerts during live call; their call is the primary input | SD §7, TS §7 |
| Compliance Officer / Reviewer | Reviews failed/flagged calls via audit output; initiates retraining; escalation recipient | CM §6, CM §10 |
| Team Lead / Management | Escalation target when agent has ≥3 critical flags in a week | TS §10, CM §10 |
| Admin | Manages rule set JSON, retraining assignment, DB / system config | TS §10 |
| End Customer | Passive — they are a party on the recorded call; their consent and understanding are checked | CM §8, CM §9 |

- No "reviewer console" UI role is explicitly described in the spec docs (the manual frames it as a Compliance Officer audit, not a software queue).

---

## 6. Required Integrations

| Integration | Purpose | Spec cite |
|-------------|---------|-----------|
| Speech-to-Text (STT) engine | Transcription from audio | TS §2 component 2 — vendor unspecified |
| NLP / LLM | Intent detection, phrase flagging beyond exact match | SD §5 — "detect intent, not just exact wording"; vendor unspecified |
| Database (relational) | Store Calls, Compliance_Results, Flags, Agents tables | TS §5 |
| Alert delivery channel | Agent screen popup; optional whisper audio | TS §7 — mechanism unspecified |
| Email system | Send auto-generated feedback email per call | TS §8 |
| CRM (implied) | Agent profiles, supplier registry | TS §5 Agents table |
| Logging / observability | Not mentioned in spec docs |

- No specific vendors named for STT, LLM, email, or database.
- No workflow engine, object storage, vector DB, or authentication system referenced.

---

## 7. Compliance Regime / Regulator

| Aspect | Detail | Cite |
|--------|--------|------|
| Primary regulator | Ofgem (Office of Gas and Electricity Markets) | CM §2.1 |
| Entity type | Third-Party Intermediary (TPI) / energy broker | CM §2.2 |
| Market | UK commercial energy (gas, electricity, water, solar, telecoms, finance, insurance) | CM §2 |
| Key obligations | Honest, transparent, fair; clear commission disclosure; no pressure selling; accurate documentation | CM §2.1 |
| Commission rules | Must disclose p/kWh or total amount; not rushed or hidden | CM §4.1 |
| Broker identity rule | Must state "Watt Utilities" (not WATT); must not impersonate supplier | CM §3, SD §3 |
| Confirmation procedure | Written confirmation email post-sale; CRM-logged | CM §8 |
| Documentation required | LOA, contract summary, T&Cs, MPAN/MPRN checks | CM §7 |
| Breach consequences | Commission clawback, supplier disputes, regulatory action, deal rejection | CM §2 |

---

## 8. Explicitly Out of Scope (per spec docs)

- The spec docs do **not** define out-of-scope items explicitly.
- The compliance manual is scoped to sales, admin, pricing, and customer-contact staff only (CM §1) — it does not cover post-sale account management or billing disputes.
- Document compliance (LOA, MPAN/MPRN checks — CM §7) is described as a manual checklist, not an AI pipeline task.
- Whisper audio alerts are marked "optional" (TS §7).
- No mention of batch reprocessing, historical call re-analysis, or appeal/override workflows.

---

## 9. Discrepancies vs. Existing Codebase

### 9a. Features the Spec Requires That the Codebase Does NOT Have (or has differently)

| Spec requirement | Codebase status | Gap |
|-----------------|-----------------|-----|
| **Real-time agent alerts** during live call (screen popup + whisper audio) | CS: no live/streaming call support; pipeline is post-upload batch | Major gap — entire real-time channel is missing |
| **Call Segmentation into 6 named stages** (Intro / Qualification / Pitch / Transfer / Verbal Contract / Close) | AR §Data Flow: metadata detection step identifies agent/supplier but no named stage segmentation in pipeline | Stage-aware rule dispatch not implemented |
| **Per-stage rule dispatch** (different rule sets run per segment) | AR: rules fetched for "script + supplier variant"; no explicit stage-scoped rule binding | Partial — supplier-scoped but not segment-stage-scoped |
| **Auto-generated feedback email** per call | CS/AR: no email-send integration mentioned anywhere | Missing integration |
| **Automation: ≥3 criticals/week → escalate agent** | CS/AR: no agent-level weekly aggregate trigger; individual call blocking only | Missing trigger |
| **Automation: retraining assignment** | Not present in codebase | Missing |
| **Risk tags: Ombudsman Risk / Mis-selling Risk / Complaint Risk / Cancellation Risk** | AR: `Call.risk_tags TEXT[]` exists; `vulnerable_customer` and `pricing_mismatch` detectors mentioned | Partial — field exists but spec-defined tag taxonomy may not be enforced |
| **Scoring tiers: Coaching (70–89)** | CS: 4-tier scoring referenced in SUMMARY context; needs verification in `compliance.py` | Likely present but unverified |
| **Agent screen popup delivery** | No frontend component or WebSocket/SSE endpoint for live agent alerts | Missing |
| **Whisper audio output** | Not referenced anywhere in codebase | Missing |

### 9b. Features the Codebase Has That the Spec Does NOT Mention

| Codebase feature | Spec mentions it? |
|-----------------|------------------|
| Multi-engine transcription tribunal (7 STT providers, WER consensus) | No — spec says STT engine, unspecified |
| Tiered Smart Agent (Gemini Flash → Claude Sonnet 4.6 escalation) | No — spec says NLP/intent detection, no LLM architecture |
| RAG / pgvector search over rule chunks, LOA chunks, supplier doc chunks | No |
| HITL reviewer console (queue, claim locks, audit trail, score overrides) | No — spec mentions Compliance Officer audit but not a software review queue |
| Hash-chain audit log (tamper-evident) | No |
| Durable Inngest workflow with memoized step retries | No |
| Supabase Auth / JWT reviewer authentication | No |
| LOA ingestion and LOA-based rule matching | No |
| Supplier doc ingestion | No |
| Deal-level rollup verdicts | No |
| Rejection factory (structured rejection records) | No |
| Idle claim sweeper cron | No |
| Observability stack (Sentry, Prometheus, Grafana, Loki) | No |
| pgvector embedding pre-filter (Wave-4 feature flag) | No |
| Vulnerable customer detector | No |
| Pricing mismatch detector | No |
| RAG ingestion workflow (rag_ingest_call_fn) | No |

### 9c. Summary Assessment

- The **spec is a high-level product brief** (~100 lines); the codebase is a significantly more mature system with architecture the spec never describes.
- The spec's most distinctive requirement — **real-time in-call agent alerts** — is entirely absent from the current codebase architecture, which is a post-upload batch pipeline.
- The spec's **6-stage call segmentation** with per-stage rule dispatch is not directly implemented; the codebase uses script/supplier-scoped rules without named stage boundaries.
- The codebase has substantial **capabilities the spec does not require**: multi-engine transcription, HITL console, RAG, LOA matching, audit log — these appear to be organic extensions beyond the original spec.
- The **scoring model** (0–100, 4 tiers) and **flag severity model** (Critical/High/Medium) are consistent between spec and codebase.
- The **database schema** in the spec (4 tables) is a simplified subset of the codebase's ~60-migration schema; no conflict, just underspecification.
