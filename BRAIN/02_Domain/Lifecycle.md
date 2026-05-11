---
created: 2026-05-10
updated: 2026-05-11
tags: [domain, lifecycle, scripts]
---

# Deal lifecycle — the 3-stage vs 4-stage rule

> **Corrected 2026-05-11.** Earlier versions of this doc (and the code)
> said "E.ON = 2 stages, others = 3 stages" — that was missing the
> Passover handover phase, which is a distinct stage in every Watt customer
> audio folder AND in the Watt AI Compliance Tech Spec §3. Fixed.

## The rule

- **E.ON Next** bundles the LOA into the Closer call → **3 stages**:
  Lead Gen → Passover → Closer
- **Every other supplier** (BGL, BG, EDF, Pozitive, Scottish Power) requires
  a separate LOA → **4 stages**: Lead Gen → Passover → Closer → Standalone LOA

Plus: any supplier may have **Amendment** or **C-Call** (corrective callbacks).
These don't count toward the required stage tally and don't block "verified".

## The 6 phases

| Code | UI label | Required? | What it is |
|---|---|---|---|
| `lead_gen` | Lead Gen | Yes (all) | Cold/warm intro, qualification, capture details |
| `passover` | Passover | Yes (all) | Warm handover from lead-gen agent to closer |
| `closer` | Closer | Yes (all) | Verbal contract reading; legally binding |
| `standalone_loa` | Standalone LOA | Yes (non-E.ON) | Separate LOA call (Letter of Authority) |
| `amendment` | Amendment | Optional | Post-sale fix (rate read wrong, name correction) |
| `c_call` | C-Call | Optional | Supplier or Watt confirmation callback |

## Filename hints at upload

The upload route `/api/calls/upload` inspects the filename basename and
classifies the call's `call_type` automatically — no manual tagging needed.

| Filename pattern (basename) | Resolves to |
|---|---|
| `lead.mp3`, `Lead Gen.mp3`, `LG.mp3`, `lg.mp3` | `lead_gen` |
| `passover.mp3`, `Passover.mp3` | `passover` |
| `verbal.mp3`, `closer.mp3`, `full call.mp3` | `closer` |
| `loa.mp3`, `Letter of Authority.mp3` | `standalone_loa` |
| `c call.mp3`, `c_call.mp3`, `c-call.mp3` | `c_call` |
| `amendment.mp3` | `amendment` |
| anything else (no hint) | `full` (= covers lead_gen + passover + closer for E.ON) |

The frontend `L7Form` may also pass `call_type` explicitly when the
reviewer toggles off auto-detect; that overrides the filename hint.

## In code

**Backend** — `backend/app/deal_lifecycle.py`:

```python
SUPPLIER_PHASE_MATRIX: dict[str, list[str]] = {
    "E.ON Next":  ["lead_gen", "passover", "closer"],                     # 3 stages
    "British Gas":   ["lead_gen", "passover", "closer", "standalone_loa"],   # 4 stages
    "Scottish Power":["lead_gen", "passover", "closer", "standalone_loa"],
    "EDF":           ["lead_gen", "passover", "closer", "standalone_loa"],
    "Pozitive":      ["lead_gen", "passover", "closer", "standalone_loa"],
    "BGL":           ["lead_gen", "passover", "closer", "standalone_loa"],
}
```

`derive_lifecycle_status(deal, calls)` returns:

| Returned | Meaning |
|---|---|
| `open` | no qualifying call yet |
| `lead_gen_done` | Lead Gen done; nothing else yet |
| `passover_done` | Passover landed; closer still pending |
| `closer_done` | Closer landed; required follow-up still missing (e.g. standalone LOA for non-E.ON) |
| `verified` | every required stage finalised |
| `amendment_done` / `c_call_done` | corrective post-verification states |
| `rejected` | terminal; manual override |

A `call_type="full"` recording is treated specially — counts as covering
`lead_gen + passover + closer` simultaneously, so a single full-call deal
verifies under the E.ON-style bundled flow.

**Frontend** — `frontend-v3/src/app/(admin)/customers/[slug]/page.tsx`:

```ts
const _SUPPLIER_REQUIRED_PHASES: Record<string, string[]> = {
  "E.ON Next":      ["lead_gen", "passover", "closer"],                     // 3
  "British Gas":    ["lead_gen", "passover", "closer", "standalone_loa"],   // 4
  // …
};
```

Plus a corrective set `["c_call", "amendment"]` appended at the end of
the workflow bar — visible but not counted toward the "N-stage" headline.

## Where it shows up in the UI

- **`/workflow`** — new dedicated reference page (sidebar → System → Workflow).
  Single canonical explanation with all 6 phases, per-supplier required-stage
  diagrams, filename hints, and the derivation contract.
- **`/customers/[slug]`** — each deal renders a `WorkflowBar` with N steps.
  Header reads "3-stage workflow · E.ON Next" or "4-stage workflow · British
  Gas" with a hover tooltip explaining the rule.
- **`/customers`** help banner — quick summary + link to `/workflow`.

## Phase labels (snake_case → human)

```
lead_gen        → "Lead Gen"
passover        → "Passover"
closer          → "Closer"
standalone_loa  → "Standalone LOA"
amendment       → "Amendment"
c_call          → "C-Call"
```

See [[02_Domain/Scripts]] for which scripts cover which phase.
See [[02_Domain/Watt_Compliance]] for the rejection codes that fire per phase.
