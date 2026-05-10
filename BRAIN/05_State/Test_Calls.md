---
created: 2026-05-10
updated: 2026-05-10
tags: [state, test-data]
---

# Test calls — current state

## In production database

| Call ID | File | Status | Score | Supplier | Customer | Agent | Deal |
|---|---|---|---|---|---|---|---|
| `4195b6ee-…` | Crosby grange lead gen call (2).mp3 | completed | 0/3 | E.ON Next | J. Fitzsimons | Parat | `0dba3d03-…` |
| `42a89a59-…` | Crosby grange lead gen call.mp3 | **failed** | — | — | — | — | `37088933-…` |
| `f57123db-…` | Evangelical church.mp3 | completed | 2/3 | E.ON Next | Christopher Neil Bank | (Afak post-Quality-Agent) | `bc573975-…` (the survivor) |
| `30a983fb-…` | Evangelical church 2nd Call Passover.mp3 | completed | 0/3 | E.ON Next (inherited) | Christopher | (was Christopher pre-fix; corrected to Afak by Quality Agent) | merged into `bc573975-…` |
| `75645f4c-…` | Evangelical church LOA.mp3 | completed | 0/3 | E.ON Next | Christopher Neil Banks | Zach | merged into `bc573975-…` |

## Test files available locally
At `compliance-docs/COMPLIANCE XAI/`:
- aycliffe & peter lee.mp3
- Crosby grange lead gen call.mp3 ← already tested
- CROSBY GRANGE PROPERTIES.mp3
- Evangelical church*.mp3 (×3) ← already tested + merged
- J Preston.mp3
- Matte Black London.mp3
- **Mohammad Hanif Ta Hanif Motors -  2548832208 call 1.mp3** ← recommended for next demo (single fresh customer)
- Mohammad Hanif Ta Hanif Motors -  2548832208 call 2.mp3
- Mr J P and Mr C r.mp3
- Ms Bonnie Clarke.mp3
- Nick ferris skip hire Rejected.mp3 (guaranteed-fail; good for rejection-flow demo)
- Peter hyett.mp3
- Westbury Village Hall 1st call.mp3
- Westbury Village Hall 2nd call.mp3

## Demos to show tomorrow

**Demo 1 — Cross-call merge (the Quality Agent showcase):**
1. Open `https://compliance-agent-mu.vercel.app/customers/dorothy's evangelical church`
2. Show the unified rollup: 3 calls, 1 deal, supplier `E.ON Next`
3. Open browser console / inspector — point at the response showing all 3 calls grouped
4. Tell the story: per-call detection produced 3 different business names, the Quality Agent (Opus 4.7) read them together and produced ONE canonical record with confidence 0.92 + a written reason

**Demo 2 — PipelineTimeline (visualising AI work):**
1. Open any call's detail page (`/calls/<id>`)
2. Point at the 5-stage Pipeline Timeline panel
3. Each stage has status icon + AI output + tooltip
4. Show that it makes the AI's work transparent to the reviewer

**Demo 3 — N-stage workflow rule:**
1. Open `/customers/dorothy's evangelical church` → deal card shows "**2-stage workflow · E.ON Next**" because E.ON bundles LOA into Closer
2. (Hypothetically — if a BG call were uploaded, it'd show "**3-stage workflow · British Gas**")
3. Hover the label to read the explanation
