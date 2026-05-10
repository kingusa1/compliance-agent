---
created: 2026-05-10
updated: 2026-05-10
tags: [domain, lifecycle, scripts]
---

# Deal lifecycle — the 2-stage vs 3-stage rule

## The rule
- **E.ON Next** bundles the LOA into the Closer call → **2 stages**: Lead Gen → Closer
- **Every other supplier** (BGL, BG, EDF, Pozitive, Scottish Power) requires a separate LOA → **3 stages**: Lead Gen → Closer → Standalone LOA

Plus: any supplier may have **Amendment** or **C-Call** (corrective callbacks).

## In code
**Backend** — `backend/app/watt_compliance/taxonomy.py`:
```python
SUPPLIER_PHASE_MATRIX = {
    Supplier.EON_NEXT:        ["lead_gen", "closer"],
    Supplier.BGL:             ["lead_gen", "closer", "standalone_loa"],
    Supplier.BRITISH_GAS:     ["lead_gen", "closer", "standalone_loa"],
    Supplier.EDF:             ["lead_gen", "closer", "standalone_loa"],
    Supplier.POZITIVE:        ["lead_gen", "closer", "standalone_loa"],
    Supplier.SCOTTISH_POWER:  ["lead_gen", "closer", "standalone_loa"],
}
```

**Frontend** — `frontend-v3/src/app/(admin)/customers/[slug]/page.tsx`:
```ts
const _SUPPLIER_REQUIRED_PHASES: Record<string, string[]> = {
  "E.ON Next": ["lead_gen", "closer"],
  "EON":       ["lead_gen", "closer"],
  // … every other supplier → 3 stages
}
function workflowStepsFor(supplier) { … }
```

## Where it shows up in the UI
- **Customer detail page** — each deal has a `WorkflowBar` with N steps. Header reads e.g. **"2-stage workflow · E.ON Next"** with a hover tooltip:
  - "E.ON Next bundles the LOA into the Closer call, so this deal needs 2 stages: Lead Gen → Closer."
  - "British Gas requires a separate LOA call after the Closer, so this deal needs 3 stages: Lead Gen → Closer → Standalone LOA."

## Phase labels (snake_case → human)
```
lead_gen        → "Lead Gen"
closer          → "Closer"
standalone_loa  → "Standalone LOA"
amendment       → "Amendment"
c_call          → "C-Call"
```

See [[02_Domain/Scripts]] for which scripts cover which phase.
See [[02_Domain/Watt_Compliance]] for the rejection codes that can fire per phase.
