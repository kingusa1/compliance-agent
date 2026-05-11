---
created: 2026-05-11
updated: 2026-05-11
tags: [session, log, workflow, passover, supplier-matrix]
session_date: 2026-05-11
---

# Session — 2026-05-11 (workflow clarity)

> User correction: "E.ON 3 steps, others 4 steps, not 3" → the docs and
> code were wrong. The missing phase was **Passover** (warm handover
> between lead-gen and closer). Plus housekeeping: removed a rogue
> Vercel project the CLI created when run from the wrong directory.

## Commits

| Commit | What |
|---|---|
| `e28c2f4` | feat(workflow): correct supplier-stage matrix to 3/4 + dedicated /workflow page |
| `6139d91` | fix(customer-detail): complianceTone crashes on boolean compliant |
| `071847b` | fix(types): CustomerTimelineRow.compliant accepts boolean |
| `41487a0` | fix(workflow): show required-stage count in WorkflowBar header |

## What's now live

### Backend
- `SUPPLIER_PHASE_MATRIX` rewritten: every supplier list now includes
  `passover` between `lead_gen` and `closer`.
  - E.ON / E.ON Next: `["lead_gen", "passover", "closer"]` — 3 stages
  - Everyone else: `["lead_gen", "passover", "closer", "standalone_loa"]` — 4 stages
- `_CALL_TYPE_TO_PHASE` adds `passover → passover` and `verbal → closer`.
- `_completed_phases`: `call_type="full"` credits `lead_gen + passover + closer`.
- `LifecycleStatus` Literal + `ALLOWED` DAG include `passover_done`.
- `derive_lifecycle_status` returns `passover_done` when only passover has landed.
- Upload route filename hint: `passover.mp3` → `call_type=passover` (was `closer`).
- Alembic migration `20260511_passover` updates the CHECK constraint
  (applied directly via psycopg2 against prod DB + stamped at HEAD).
- Backfilled legacy `call_type` from filenames on existing rows
  (5 passover, 4 lead_gen, 1 c_call, 2 amendment, etc).

### Frontend
- Customer detail (`customers/[slug]/page.tsx`):
  - `_SUPPLIER_REQUIRED_PHASES` rewritten with passover for all suppliers
  - `_PHASE_LABEL` adds "Passover"
  - `_stageBlurb` explains 3 vs 4 (was 2 vs 3)
  - `WorkflowBar` header shows **required**-stage count only;
    corrective steps render but say "+ N corrective" off to the side
  - Fixed `complianceTone(true)` crash (booleans now coerced safely)
- `/customers` help-banner copy: "E.ON = 3 stages, everyone else = 4 stages"
  + link to dedicated `/workflow` page.
- **NEW page `/workflow`** (sidebar → System → Workflow): canonical
  reference with the rule in one sentence, all 6 phases catalogued with
  filename hints, per-supplier required-stage blocks (E.ON gets 3, others 4),
  and the `derive_lifecycle_status` contract spelled out.

### Live verification (Playwright)
- `/workflow` page renders: headline rule emerald box · 6 stage cards ·
  6 per-supplier blocks (E.ON 3 highlighted, others 4 highlighted).
- `/customers/corner%20cuts` (E.ON Next, 2 deals, 5 calls):
  - Deal 1 → "3-stage workflow · E.ON Next" with Lead Gen / Passover /
    Closer all done, C-Call done, Amendment in progress. "4 of 5 steps · 4 done"
  - Deal 2 → "3-stage workflow" with Lead Gen done, Passover in progress.
    "1 of 5 steps · 1 done"
- `/customers` help banner shows the corrected 3/4 copy with link to Workflow.

### Lifecycle distribution post-fix
From `/api/deals` after the matrix + call_type backfill:
- 14 verified
- 3 passover_done
- 3 open
- 1 closer_done · 1 lead_gen_done · 1 amendment_done · 1 c_call_done

Previously every deal showed `verified` (incorrectly) because the matrix
didn't include passover; now the state machine actually reflects per-deal
progress.

## Vercel project housekeeping

User noticed a second Vercel project `compliance-agent-feat-wave5-deploy`
showed up in their dashboard. It was created accidentally when I ran
`vercel deploy --prod` from the repo root (no `.vercel/` there, CLI
created a new project link). Deleted via API:

```
DELETE /v9/projects/prj_9GhKM2h4sVJVP2jbgBqXtY5p4n1h
→ HTTP 204
```

Also removed the stale `.vercel/project.json` at the repo root that was
pointing at the now-deleted rogue project. `.gitignore` already covers
`.vercel`, so nothing leaked into commits.

Only `compliance-agent` (compliance-agent-mu.vercel.app) remains.

## Tracker accuracy fixes (later in same session)

User flagged that the Tracker's Awaiting Review tab showed **30 rows**
when only 28 non-compliant calls existed in `/api/stats`. Diagnosed via
direct DB probe:

1. **2 orphan rejections** with `call_id IS NULL`. The rejections table's
   `call_id` FK is `ON DELETE SET NULL` (intentional — preserves audit
   trail when a call is deleted). When I deleted two test calls earlier,
   their rejections survived with `call_id=NULL` and `customer_slug=NULL`,
   showing up as ghost rows with empty customer/agent columns.
2. **Duplicate "Rich Stevings funeral service" deal** sitting alongside
   the canonical "Richard Stebbings General Services". One call
   (c05fa857, "C call.mp3") was attached to the duplicate deal because
   the Quality Agent's bucketing key disagreed on per-call customer
   names ("David" vs "Drusilla Stebbings").

Fixes:
- **DB cleanup**: deleted both orphan rejections and merged the
  duplicate deal into the canonical one (moved the 1 call, deleted
  the empty deal).
- **Code guard**: `tracker_aggregator.build_tracker_rows` now filters
  `Rejection.call_id IS NOT NULL` on both the `awaiting_review` and
  `active` / `fixed` / `dead` tabs. Orphan rejections stay in the DB
  for audit but never surface as actionable rows. Commit `840a5e2`.

Tracker now reconciles exactly:
- Awaiting review · 28 ↔ 28 non-compliant calls
- Compliant · 8 ↔ 8 compliant calls
- Sum: 36 of 37 calls (1 failed = no rejection produced, intentional)

## Lingering follow-ups

- MPAN/MPRN + Value columns still empty on most tracker rows — the
  tracker autofill agents populate these from the deal record, which
  requires meter IDs / contract value at upload time. Legacy auto-detect
  uploads don't have them; manual entry mode does.
- Live date column populated on ~5/8 compliant rows (date_extractor
  agent worked when transcript mentioned a future date) — the other 3
  had no explicit live-date mention in the call.
- Quality Agent's customer-bucketing is name-similarity based; two deals
  for the same customer slip through when the per-call name detection
  drifts ("David" vs "Drusilla Stebbings"). Future: seed a known-customer
  list per Watt's CRM and snap-to-canonical.
- The customer detail page is the only consumer of the boolean-vs-string
  `compliant` field. If new code is added that calls a stringifier on
  this field, it must handle both shapes. Already documented in the
  type alias and in `complianceTone()` itself.
- 19 legacy calls still have `call_type='full'` — those were uploaded
  before the filename-hint pre-pass landed and their basename doesn't
  match any keyword. They still verify correctly under the
  "full counts as lead_gen+passover+closer" rule for E.ON, but for
  non-E.ON they'd need a manual re-classify to reach `verified` if a
  separate LOA was never recorded.
- Agent name normaliser threshold (0.84) doesn't merge "Alex Fitz" vs
  "Alex Fitton". Out of scope for this session; queued.
