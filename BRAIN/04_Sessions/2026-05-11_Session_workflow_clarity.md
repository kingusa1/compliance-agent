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

## Lingering follow-ups

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
