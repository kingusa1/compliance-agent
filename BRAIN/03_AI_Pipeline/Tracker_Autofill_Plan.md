---
created: 2026-05-10
updated: 2026-05-10
tags: [pipeline, agents, tracker, roadmap, autofill]
---

# Tracker Autofill Plan — make every column AI-fillable

> User direction (2026-05-10): "Fill everything with AI. Create more agents to do the task autonomously and accurate." Reference docs: `compliance-docs/COMPLIANCE XAI/Compliance tracker example.xlsx` and `Compliance Xai rejection lists.xlsx` (markdown extracts in `.planning/phase2-docs/`).

## Per-column source map

The tracker's 16 columns, where each one comes from today, and what's needed to make it 100% AI-filled.

| # | Column | Source today | AI-fillable? | Gap → Plan |
|---|---|---|---|---|
| 1 | Customer Name | `CustomerDeal.customer_name` (auto-detected via Opus 4.7 → `_step_finalize` rename) | ✅ working | Tighten via Customer-Name Specialist Agent (already on backlog as Future_Agents item #6) |
| 2 | MPAN / MPRN | `CustomerDeal.mpan_or_mprn` (regex+LLM entity extraction in `_step_finalize`) | ✅ working | Add MPAN check-digit validator (per existing Future_Agents `data_enricher.py` plan) |
| 3 | Expected Live date | `CustomerDeal.expected_live_date` — **almost always NULL today** | ⚠️ GAP | **NEW AGENT: DateExtractorAgent** |
| 4 | Deal Value (£) | `CustomerDeal.deal_value_gbp` (entity propagation) | ⚠️ partial — fails when not stated explicitly | Harden the existing entity prompt to include £/p.a. variants |
| 5 | Supplier | `Call.detected_supplier` (canonicalize_supplier on the LLM detection) | ✅ working | — |
| 6 | Rejected at | `Rejection.rejected_at` (set at rejection creation) | ✅ working | — |
| 7 | Sales Agent | `Call.agent_name` (signal-based speaker detection + Opus name extraction) | ✅ working | Tighten via Customer-Name Specialist (its sibling agent: Agent-Name Specialist) |
| 8 | Rejection Reason | `Rejection.rejection_reason` (rule_id text or LLM summary) | ✅ working | — |
| 9 | Category | `Rejection.category` (W4.7 `suggested_category` from CheckpointVerdict) | ⚠️ coverage gap — only set on FAIL/PARTIAL with suggestion | **NEW AGENT: CategoryClassifierAgent** to backfill anything missing |
| 10 | Fix Required | `Rejection.fix_required` (W4.7 `suggested_fix_required`) | ⚠️ coverage gap — same as Category | **NEW AGENT: FixRequiredAgent** |
| 11 | Fixed BY | `Rejection.fix_assignee_id` (HUMAN ASSIGNED) | ❌ stays human | — (intentional) |
| 12 | Status | `Rejection.status` (lifecycle state machine) | ✅ semi-auto | — |
| 13 | Last Action Date | `_last_action_date()` computed from rej events | ✅ auto | — |
| 14 | Deadline | `Rejection.deadline` — **never auto-set today** | ⚠️ GAP | **NEW AGENT: DeadlineComputerAgent** |
| 15 | Outcome | `Rejection.outcome` (HUMAN ASSIGNED) | ❌ stays human | — (intentional, end-state record-of-truth) |
| 16 | Notes | `Rejection.outcome_narrative` (HUMAN) | ❌ stays human | — (intentional) |

## The 3 new specialist agents

All three follow the **CallContext** pattern from `07_Tomorrow/Next_Steps.md` item #2 (the multi-agent orchestrator). They read from the shared call context, write to `Rejection` / `CustomerDeal` fields, and append to `agent_log` for audit.

### Agent A — DateExtractorAgent

**Role:** extract dates from the transcript that the customer or agent commits to.

**Inputs:** transcript, call_type (lead_gen / closer / loa / etc.), supplier, customer_name.

**Output:** structured object — `{ expected_live_date: ISO_date | null, contract_start_date: ISO_date | null, end_of_contract_date: ISO_date | null, confidence: 0..1, evidence_quote: string }`.

**Where it writes:**
- `CustomerDeal.expected_live_date` ← from `expected_live_date`
- `CustomerDeal.contract_end_date` (new column? or reuse) ← from `end_of_contract_date`

**Cheap-path optimisation:** before LLM, run a deterministic regex pass over the transcript looking for `(\d{1,2})(?:st|nd|rd|th)?\s+(of\s+)?(Jan|Feb|...|December)` + `(\d+)\s+(weeks?|months?|days?)` patterns. Skip the LLM if regex finds 0 candidates.

**LLM cost:** Haiku 4.5 is sufficient — date extraction doesn't need Opus. Estimated ~$0.0003/call.

**Files:**
- New: `backend/app/agents/date_extractor.py`
- Wire in: `backend/app/pipeline.py:_step_finalize` after the existing entity propagation block

### Agent B — CategoryClassifierAgent + FixRequiredAgent (one combined agent)

These two share so much context they should be one agent — name `RejectionAdvisorAgent`. The agent looks at a non-compliant call and emits both the `category` and the `fix_required` recommendation.

**Inputs:** transcript snippet around the failure, failed checkpoint(s), supplier, agent_name, customer_name, rule_id.

**Output:** `{ category: enum, fix_required: string, severity: enum, confidence: 0..1, evidence_quote: string }`.

**Categories** (taken from the rejection_lists.xlsx extract) — **the canonical 4 master buckets:**
- `ADMIN ERROR` — wrong name on contract, missing LOA, etc.
- `PROCESS FAILURE` — bacs denied, wrong DD, contract not sent, etc.
- `COMPLIANCE BREACH` — TPI mis-disclosure, mis-selling, vulnerability not handled
- `RE-WORK NEEDED` — supplier sent it back asking for fix

**Where it writes:**
- `Rejection.category`
- `Rejection.fix_required` (1-2 sentence operations-team-tone instruction)

**LLM:** Opus 4.7 — coverage matters here, the rejection's category drives reviewer triage. ~$0.005/call.

**Files:**
- New: `backend/app/agents/rejection_advisor.py`
- Wire in: `backend/app/pipeline.py:_step_finalize`, after rejection rows are created
- Also: backfill mode — `POST /api/admin/backfill-rejection-advisor` (similar shape to `/api/admin/quality-resolve`) walks all existing rejections with NULL `category` or `fix_required` and fills them in

### Agent C — DeadlineComputerAgent

**Role:** assign a sensible deadline to every rejection. Mostly deterministic, but uses the Rejection-Advisor's `severity` output.

**Inputs:** `severity` from RejectionAdvisorAgent, `rejected_at`, `expected_live_date`, supplier (some suppliers have known SLAs).

**Logic:**
| Severity | Deadline |
|---|---|
| `CRITICAL` (compliance breach) | min(`rejected_at` + 24 h, `expected_live_date` − 24 h) |
| `HIGH` (process failure blocking go-live) | min(`rejected_at` + 72 h, `expected_live_date` − 24 h) |
| `MEDIUM` (admin rework) | `rejected_at` + 5 business days |
| `LOW` (re-work / cosmetic) | `rejected_at` + 10 business days |

**Where it writes:** `Rejection.deadline`.

**LLM:** none — pure compute. Free.

**Files:**
- New: `backend/app/agents/deadline_computer.py`
- Wire: same pipeline step as RejectionAdvisor (immediately after, since it needs that agent's severity)

## Coordinator changes

`backend/app/agents/coordinator.py` (per `Future_Agents.md` plan) gains 3 new specialists in its registry:

```python
SPECIALISTS = {
    "quality": QualityAgent,           # already shipped
    "call_type": CallTypeAgent,        # planned
    "decision_maker": DecisionMakerAgent,
    "customer_intent": CustomerIntentAgent,
    "verdict_reviewer": VerdictReviewerAgent,
    "data_enricher": DataEnricherAgent,
    # NEW (this plan):
    "date_extractor": DateExtractorAgent,
    "rejection_advisor": RejectionAdvisorAgent,
    "deadline_computer": DeadlineComputerAgent,
}
```

The coordinator's order-of-operations for a non-compliant call:

```
upload → transcribe → score (existing) →
  rejection_factory (creates Rejection rows) →
  rejection_advisor (fills category + fix_required) →
  deadline_computer (computes deadline) →
  date_extractor (fills expected_live_date) →
  quality_agent (cross-call merge / identity) →
  finalize
```

For a compliant call:
```
upload → transcribe → score → date_extractor → quality_agent → finalize
```

## Migration / backfill path

The DB has 5 calls + 4 deals + an unknown number of rejections from the old pipeline that don't have category/fix_required/deadline/expected_live_date. The plan ships in 3 phases:

1. **Phase 1 — agents wired** (1 day): all 3 new agents implemented, registered with coordinator, run on every NEW upload. Verified locally + smoke tested in prod.
2. **Phase 2 — backfill endpoints** (½ day): admin endpoints `POST /api/admin/backfill-categories`, `POST /api/admin/backfill-deadlines`, `POST /api/admin/backfill-dates` walk all calls/rejections with the field NULL and run the relevant agent. Idempotent. Reviewer can run them once after Phase 1 ships.
3. **Phase 3 — reviewer overrides become first-class** (½ day): tracker UI gets per-cell "AI-suggested → reviewer-confirmed" provenance pills, similar to the existing `field_sources` pattern. Reviewer clicks → cell becomes editable → save → field_sources gets `{column: "human:<reviewer_id>"}` instead of `{column: "ai:<agent_id>"}`.

Total: ~2 days of focused work to get every column AI-fillable AND keep the reviewer override path clean.

## Cost estimate

Per call, AI agent layer adds:
- DateExtractorAgent (Haiku): ~$0.0003
- RejectionAdvisorAgent (Opus, only on non-compliant calls): ~$0.005
- DeadlineComputerAgent (no LLM): $0
- QualityAgent (Opus, already running): ~$0.003 (unchanged)

**Total marginal cost per non-compliant call: ~$0.008** (~30% increase over today's pipeline). Compliant calls only pay for DateExtractor: +$0.0003.

For Watt's volume (~estimate 200 rejections/month), the new agents cost ~$1.60/month. Trivial.

## Acceptance criteria

After Phase 2 backfill is run, **every row** in `/api/tracker/rows` for **every existing call** should have:
- `category` ≠ NULL (auto + backfilled)
- `fix_required` ≠ NULL  
- `deadline` ≠ NULL
- `expected_live_date` ≠ NULL where the transcript mentions it (~70-80% expected coverage)

Only HUMAN-only columns (`Fixed BY`, `Outcome`, `Notes`) remain NULL until a reviewer fills them.

## Cross-references

- Per-column source: `backend/app/tracker_aggregator.py` `_rejection_row()` (line 71) and `_compliant_row()` (line 116)
- Multi-agent pattern: [[../07_Tomorrow/Next_Steps]] section "Multi-agent orchestrator"
- Existing Quality Agent: [[Quality_Agent]]
- Existing W4.7 suggested fields: `backend/app/schemas.py` `CheckpointVerdict.suggested_category` / `.suggested_fix_required`
