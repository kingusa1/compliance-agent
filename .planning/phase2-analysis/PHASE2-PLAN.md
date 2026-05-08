# Phase 2 — Compliance Material Integration

**Generated:** 2026-05-09
**Inputs:** A-system-spec.md, B-compliance-guide.md (27 rejection cats), C-phrase-dataset.md (120 examples), D-supplier-scripts.md (14 scripts), tracker example XLSX, real rejection list XLSX
**Goal:** wire the compliance system to match the Watt Utilities Ofgem-regulated TPI workflow exactly, so the moment a recording is uploaded the agent flags it correctly.

## What's the system actually doing?

Internal compliance review for an Ofgem-regulated UK energy-broker TPI (Watt Utilities) covering B2B only. Every call goes through:

```
upload → transcribe → segment → phrase-detect → LLM analysis → score → rejection → tracker row
```

Each rejection follows a real-world resolution recipe (amendment call, new LOA, etc.) — the tracker captures this state machine.

## Canonical taxonomy (locked from the source docs)

### Call types (replaces frontend `intro/qualification/pitch/transfer/close`)

| Code | Folder name in user data | Description |
|------|--------------------------|-------------|
| `lead_gen` | Lead Gen.mp3 / LG / lg | Lead-generation call (cold contact / interest qualification) |
| `passover` | passover.mp3 / Passover.mp3 | Handover from lead-gen agent to closer |
| `closer` | (verbal / closer scripts) | Closer call where pricing is presented |
| `verbal` | verbal.mp3 / Verbal | Verbal contract confirmation (legally binding script) |
| `loa` | loa.mp3 / Letter of authority *.pdf | Letter of Authority (verbal or written) |
| `c_call` | c call.mp3 / C call.mp3 | Compliance call (post-sale verification) |
| `amendment` | amendment.mp3 | Post-sale amendment call (fixing a verbal/LOA) |
| `full` | full call.mp3 / FULL CALL.mp3 | End-to-end recording (all stages in one file) |

### Rejection categories (the **4 master categories** from `Compliance tracker example.xlsx`)

| Category | When to use |
|----------|-------------|
| `ADMIN_ERROR` | Wrong name, wrong company details, postcode mismatch, MPAN typo — paperwork issues |
| `PROCESS_FAILURE` | BACS denied, no LOA, COT not actioned, debt on account, prepayment meter, domestic meter — workflow blockers |
| `COMPLIANCE_ISSUE` | Identity not stated, vulnerable customer, no authority, owner didn't authorise — Ofgem standards breached |
| `VERBAL_SALES_ERROR` | Missed lines in verbal, wrong rates, didn't read DD guarantee, rushed script — sales-script delivery failures |

### Rejection reasons (the **27 detailed types** from B-compliance-guide.md)

`R01_IDENTITY_FAILURE` … `R27_VERBAL_LOA_NOT_SUPPLIER_APPROVED` — see [B-compliance-guide.md](B-compliance-guide.md#9-rejection-categories-flat-numbered-list) for the full table. Each reason maps to one of the 4 master categories.

### Severity tiers (from C-Phrase-Dataset)

| Tier | Action | Frontend label |
|------|--------|----------------|
| `Critical` | BLOCK + escalate | 🔴 Block |
| `High` | REVIEW (manual) | 🟠 Review |
| `Medium` | COACH (training note only) | 🟡 Coach |

### Tracker status pipeline (from `Compliance tracker example.xlsx`)

`🔴 Not Started` → `🟠 In Progress` → `🟢 Fixed – Resubmitted` → final outcome (`Fixed & Submitted` / `Customer Lost` / `Cancelled` / `Not Recoverable`)

Deadline is auto-set to `Rejected At + 2 days`.

### Suppliers in scope (14 scripts, 6 suppliers)

| Supplier | Scripts | Latest version |
|----------|---------|----------------|
| BGL (British Gas Lite) | acquisition only | V7 (V6 deprecated) |
| British Gas (core) | acquisition + renewal/upgrade | V0.2 acq / V03 ren |
| EDF | acquisition + preamble | V11 (Aug 2024) |
| E.ON Next | acq/ren elec + acq/ren gas + LOA | Jan 2026 (undated deprecated) |
| Pozitive | acquisition + renewal | undated PE |
| Scottish Power | acquisition + renewal + multisite | Oct 2024 |

## Implementation priorities

### P0 — Must ship before user uploads a record (4 hours)

1. **Frontend `CallType` enum** — replace with the 8 canonical codes above. File: [`frontend-v3/src/lib/schemas/l7-intake.ts`](../../frontend-v3/src/lib/schemas/l7-intake.ts).
2. **Backend rejection taxonomy** — define the 4 categories + 27 detailed reasons as Python `Enum`s in `app/compliance/taxonomy.py` (new module). Update `Rejection` model `category` column to enum-validated.
3. **Phrase detection regex pre-pass** — implement the 6 seed regexes from C-Phrase-Dataset §6 in `app/compliance/phrase_regex.py` (new module). Run BEFORE LLM. Short-circuit critical violations to BLOCK without LLM cost.
4. **System prompt rewrite** — replace `V1_PROMPT` in `app/analysis.py` with a Watt-grounded prompt that lists all 8 Standards, 27 rejection reasons, the 4 severity rules, and the 4 master categories. Reference `app/compliance/taxonomy.py` enum names.
5. **RAG-ingest the 14 supplier scripts** — add `app/compliance/supplier_seed.py` that on first boot reads `compliance-docs/Supplier Scripts/` + extracts text, calls existing `ingest_script` (in `app/rag/ingest.py`) with metadata `{supplier, script_type, call_class, version, effective_from, deprecated}`.
6. **Script auto-detection** — `app/compliance/script_detect.py` (new): hard keyword match + call-class detect + script-type detect from D-supplier-scripts.md §5. Hooked into `step_detect_metadata`.

### P1 — Strongly recommended (3-4 hours)

7. **Tracker output schema alignment** — match the XLSX columns. The `Rejection` table already has most fields; add `mpan_mprn`, `expected_live_date`, `deal_value_gbp`, `sales_agent`, `fix_required`, `fixed_by`, `last_action_date`, `deadline`, `outcome`. Set `deadline = rejected_at + 2 days` via DB trigger or service-layer default.
8. **Per-stage rule dispatch** — extend `analyze_all_checkpoints` so different rule subsets run per call_type:
   - `lead_gen` → Cat-1 to Cat-6 (identity / authority / pricing / market / pressure / supplier-claims)
   - `verbal` → Cat-7 to Cat-10 (script framing / commission / contract terms / consent)
   - `loa` → R20-R23, R27 (LOA-specific)
   - All call types → script delivery (Cat-11)
9. **Risk-tag enforcement** — tighten `Call.risk_tags` so it can only contain `ombudsman_risk | mis_selling_risk | complaint_risk | cancellation_risk` (Postgres CHECK constraint or Pydantic validator).
10. **Auto-feedback email after analysis** — minimal SMTP wrapper in `app/notifications/feedback_email.py`. Triggered by `call/finalized` Inngest event. Body uses the rejection.fix_required text directly (matches the tone of the actual rejection list XLSX).

### P2 — Nice-to-have (defer if time tight)

11. Agent escalation trigger (≥3 criticals/week → email lead) — Inngest weekly cron.
12. Domestic-meter auto-rejection (PROCESS_FAILURE / R12).
13. Prepayment-meter hard stop (PROCESS_FAILURE / R13).
14. Pozitive Micro-business threshold check (turnover < £1,769,200, etc.)
15. Real-time agent alerts — **DEFERRED** (frontend design FROZEN, requires WebSocket/SSE).

## What gets removed (redundancy cleanup)

| Item | Where | Why |
|------|-------|-----|
| Sales-funnel CallType values (`intro`, `qualification`, `pitch`, `transfer`, `close`) | `frontend-v3/src/lib/schemas/l7-intake.ts:60` | Don't match the actual workflow ("Lead Gen" was the user's "lead pH") |
| DOB confirmation as universal requirement | (if it exists) per L7Form | The Watt compliance guide explicitly does NOT require DOB; some sole-trader credit checks do. Make it conditional on credit-check branch. |
| Generic supplier names in seeded data | `backend/app/...` (need to audit) | The 6 supported suppliers are explicit |

## Files to create

```
backend/app/compliance/
  __init__.py
  taxonomy.py            # 4 categories + 27 reasons + severity enum
  phrase_regex.py        # 6 seed regexes from Phrase Detection Dataset
  script_detect.py       # supplier + script_type + call_class detection
  supplier_seed.py       # bootstrap RAG ingestion of 14 supplier scripts
  prompts.py             # Watt-grounded system prompts
backend/app/notifications/
  feedback_email.py      # auto-email after analysis (P1)
backend/scripts/
  seed_compliance_data.py  # CLI to (re-)run RAG seed + taxonomy migration
```

## Files to update

```
frontend-v3/src/lib/schemas/l7-intake.ts       # CallType enum
backend/app/analysis.py                         # replace V1_PROMPT, hook regex pre-pass
backend/app/checkpoint_analyzer.py              # per-stage dispatch (P1)
backend/app/compliance.py                       # use new taxonomy
backend/app/models.py                           # Rejection table extra columns (P1)
backend/app/rejections_routes.py                # filter by new categories (P1)
backend/alembic/versions/<new>.py               # migration for new columns
```

## Test plan

1. Re-run `pytest` on Linux (Railway) — should be 549/549 passing (Windows-only teardown locks don't apply).
2. Type-check + `next build` — green.
3. Use `Crosby grange lead gen call.mp3` (343 KB, smallest test audio) as the smoke-test fixture.
4. Once user supplies Deepgram + Anthropic + Supabase keys: upload Crosby Grange recording end-to-end, expect: transcribed → segmented as `lead_gen` → identity check (Watt Utilities mentioned? yes per actual rejection list) → score → no rejection if clean; rejection row matching the actual XLSX feedback (`"You did not state that you were from watt utilities at the start of call"`) if applicable.
5. Compare verdict against the human-rejection note in `Compliance Xai rejection lists.xlsx` for that call. Must match within ±1 severity tier.

## Definition of Done

- [ ] User uploads `Crosby grange lead gen call.mp3`. Pipeline runs end-to-end. Verdict + rejection row appear matching the human note.
- [ ] User uploads `Evangelical church.mp3` (full LOA + verbal + amendment). Each segment correctly classified; rejections match the human review note.
- [ ] All 14 supplier scripts indexed in pgvector under `scripts:{supplier}:{type}:{class}` namespace.
- [ ] Frontend CallType dropdown shows the 8 canonical types only.
- [ ] Tracker output XLSX matches the user's `Compliance tracker example.xlsx` schema.
- [ ] No regressions in the 399 backend tests that already pass.
