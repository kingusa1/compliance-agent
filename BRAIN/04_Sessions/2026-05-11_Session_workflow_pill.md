---
created: 2026-05-11
tags: [session, workflow, ui, aly-blockers]
---

# Session 2026-05-11 — Workflow pill + Aly ask

## What shipped

### UI — color-coded 3-vs-4 stage indicator everywhere

- **`frontend-v3/src/lib/workflow.ts`** — single source of truth that
  mirrors backend `deal_lifecycle.SUPPLIER_PHASE_MATRIX`. Exports:
  `isEonSupplier`, `requiredPhasesFor`, `workflowStageCount`,
  `workflowStepsFor`, `workflowSummary`, `workflowTone`.
- **`frontend-v3/src/components/design/WorkflowTypePill.tsx`** — new
  color-coded pill component. **Emerald** `3-stage · E.ON · LOA bundled`
  OR **blue** `4-stage · British Gas · separate LOA`. Tone, count, and
  display label all derived from the supplier label passed in.
- **`/customers` list** — new "Workflow" column rendering compact pill.
- **`/customers/[slug]`** — pill in hero next to supplier name; the
  per-deal `WorkflowBar` now uses the shared util and adds two sublabels:
  - `+ LOA bundled` underneath the Closer step (E.ON only)
  - `separate LOA call` underneath the Standalone LOA step (non-E.ON)
- **`/calls/[id]`** (reviewer) — compact pill in header beside detected
  supplier.

### Aly ask consolidated

`comms/2026-05-11_Aly_ask.md` — 4 blockers in one paste-ready Slack/WhatsApp block:

1. E.On parent vs E.On Next — same supplier or split?
2. Standalone LOA — ever recorded as standalone audio?
3. 5 supplier scripts need `1. 2. 3.` reformat (BGL V7, BG Acq V0.2, BG
   Renewal V03, EDF V11, Pozitive)
4. Sample audio for non-E.ON closes (BG / BGL / EDF / SP / Pozitive)

## How the AI drives this

The supplier label on every Call comes from `pipeline._step_detect_metadata` →
`detect_supplier(transcript)` (LLM) → `canonicalize_supplier()` (enum). The
`detected_supplier` field is then read by the React tree and fed into
`workflowTone(supplier)` and `workflowStageCount(supplier)`. **No manual
tagging** — the workflow type is fully AI-driven end-to-end.

When a supplier hasn't been detected yet (e.g. transcript pending) the
pill renders neutral `? stages` so reviewers don't accidentally apply the
wrong rule to an unclassified call.

## Verification (Playwright on prod)

- `/customers` — 18 rows, every E.ON Next row shows `3-stage` emerald pill
  with tooltip "E.ON Next bundles the LOA into the Closer call, so this
  deal needs 3 stages: Lead Gen → Passover → Closer."
- `/customers/little dowran farm` — hero shows `3-stage · E.ON Next · LOA
  bundled`; WorkflowBar shows "3 required · 2 corrective · hover for
  details"; Closer step has `+ LOA bundled` sublabel.
- `/calls/f017bb03-…` — header shows compact `3-stage` pill next to
  "E.ON Next · agent Sean Robbins · …".
- In-browser rule check: tested all 6 supplier labels — E.ON variants → 3,
  BG / BGL / EDF / SP / Pozitive → 4, unknown → `? stages`.

## Deploy ops gotcha

GitHub auto-deploy is NOT wired on the Vercel project (`deployHooks: []`).
Pushes to `main` succeed but Vercel doesn't pick them up. Trigger
manually:

```bash
VERCEL_TOKEN=$(grep -oE '"token":\s*"[^"]+"' /c/Users/kingu/AppData/Roaming/com.vercel.cli/Data/auth.json | head -1 | cut -d'"' -f4)
SHA=$(git rev-parse HEAD)
curl -sX POST --ssl-no-revoke \
  "https://api.vercel.com/v13/deployments?forceNew=1&teamId=team_fNQJtpp1M2P2dkcoWvQIziCr" \
  -H "Authorization: Bearer $VERCEL_TOKEN" -H "Content-Type: application/json" \
  -d "{\"name\":\"compliance-agent\",\"project\":\"prj_eHIyIFyxusNdCd6mR9Ff469NrcKO\",\"target\":\"production\",\"gitSource\":{\"type\":\"github\",\"repoId\":1233382040,\"ref\":\"main\",\"sha\":\"$SHA\"}}"
```

`repoId` is `1233382040` (kingusa1/compliance-agent), NOT `1024258735`.

## Next session

- Wait for Aly's reply to the 4-blocker ask
- Author V2 supplier-script checkpoints for the 8 parseable scripts
  (E.ON × 5, Scottish Power × 3) — kills 8 of the 12 `Script.checkpoints: "[]"`
  placeholders. Need careful per-PDF authoring against the canonical
  Standards (S1–S8) and 27 rejection codes; pending Aly's clarity on the
  5 unparseable scripts to do all 13 at once.
- Inline-edit cells on `/tracker` for MPAN/Value/Live-date manual fill
- Reviewer sign-off flow (claim → verdict → reviewed_at)
