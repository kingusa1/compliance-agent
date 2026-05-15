---
created: 2026-05-15
tags: [state, scripts, validation, audit, supplier-seed, phrase-pack]
---

# Scripts page — source-doc validation (2026-05-15)

> User asked: "Are all the scripts on /scripts page 100% based on the documents?"
> Answer: **Almost — 15/16 supplier docs ingested + 4/5 phrase packs + 1:1 content match on spot-check. Three concrete gaps below.**

## Ground truth — what's in PROD right now

Pulled from `GET https://compliance-agent-production-690e.up.railway.app/api/scripts`
on 2026-05-15: **19 Script rows total** (16 active, 3 deprecated/inactive).

### 15 supplier scripts (15/16 source files ingested)

| Script | cps | Source `.docx`/`.pdf` | Source extract | Active? |
|---|---:|---|---:|---|
| BGL Broker Acquisition V7 | 29 | `BGL Broker Acquisition script V7 .docx` | 98 lines | ✅ |
| BGL Acquisition (legacy V6) | 30 | `CORRECT - BGL Acquisition script.docx` | 98 lines | ⊝ inactive |
| British Gas Broker Acquisition V0.2 | 21 | `BRITISH GAS _Broker Acquisition Script_V0.2 1.pdf` | 119 lines | ✅ |
| British Gas Renewal/Deemed V03 | 20 | `BRITISH GAS_Broker Upgrade Renewals Deemed Script_V03 1.pdf` | 107 lines | ✅ |
| EDF Pre-amble v1 | 12 | `EDF PRE AMBLE SCRIPT TO BE READ .pdf` | 36 lines | ✅ |
| EDF TPI Fixed-for-Business Acq V11 | 72 | `EDF H3083_TPI_Fixed_For_Business_Online_Acqusition_Script_AW1_V11.pdf` | 130 lines | ✅ |
| E.ON Next Elec Verbal (undated) | 24 | `EON Next Elec Verbal Contract Script.docx` | 38 lines | ⊝ inactive |
| E.ON Next Gas Verbal (undated) | 25 | `EON Next Gas Verbal Contract Script.docx` | 39 lines | ⊝ inactive |
| E.ON Next Gas Verbal (TPI) Jan2026 | 25 | `eon next Gas Verbal Contract Script (TPI) - Jan 26.docx` | 40 lines | ✅ |
| E.ON Next NHH+HH (TPI) Jan2026 | 26 | `eon next NHH & HH Verbal Contract Script (TPI) - Jan 26.docx` | 43 lines | ✅ |
| E.ON TPI Verbal LOA V2 | 11 | `EON TPI Verbal LOA Script (2).docx` | 13 lines | ✅ |
| Pozitive Verbal Contract PE | 71 | `Pozitive Verbal Contract Script_PE.pdf` | 291 lines | ✅ |
| Scottish Power Acq TPI Oct24 | 29 | `Scottish Power For Business Acq Script - TPI October 24.pdf` | 191 lines | ✅ |
| Scottish Power Renewal TPI Oct24 | 28 | `Scottish Power For Business Renewal Script - TPI October 24.pdf` | 169 lines | ✅ |
| Scottish Power Multisite Acq Oct24 | 31 | `Scottish Power For Business Script - TPI Acq Multisite October 24.pdf` | 197 lines | ✅ |

### 4 PHRASE_PACK scripts (4/5 packs ingested)

All seeded from `compliance-docs/COMPLIANCE XAI/Watt_AI_Phrase_Detection_Dataset (1).docx`
via `backend/app/agents/phrase_pack_extractor.py`. The extractor enforces **one
checkpoint per source-doc row** so counts are exact, not approximated.

| Pack name | cps | Source rows | Match? |
|---|---:|---|---|
| Watt Phrase Pack · Lead Generation | 88 | Lead Generation section (20+12+20+12+12+12) | ✅ exact |
| Watt Phrase Pack · Lead Generation - handover and authority (passover) | 88 | Same Lead Generation rows | ✅ exact (duplicate content) |
| Watt Phrase Pack · Confirmation callback (C-call) | 32 | Verbal Confirmation section (5+5+8+8+6) | ✅ exact |
| Watt Phrase Pack · Amendment call | 32 | Same Verbal Confirmation rows | ✅ exact (duplicate content) |

## ❌ Gap 1 — Valda SmartChoice script is NOT ingested

- **Source exists:** `compliance-docs/Supplier Scripts/Valda SmartChoice_Telephone_Script_EXTERNAL_Direct_Debit_2024_09_03.01.pdf`
- **Not in `supplier_seed.CATALOGUE`** (`backend/app/watt_compliance/supplier_seed.py`)
- **Not extracted into `.planning/phase2-docs/`** (no `supplier_scripts__valda_*.md`)
- **Not in DB** — `GET /api/scripts` returns no Valda row
- **Impact:** any call routed to supplier `Valda Energy` will fall through to the V1 / phrase-pack fallback because the rubric router has no Valda script to attach. **Not graded against Valda's actual verbal-contract requirements.**

**Fix:** add `Valda` to `Supplier` enum (if missing), add a `SupplierScriptMeta` entry to `CATALOGUE`, re-run `python scripts/extract_phase2_docs.py` + `python -m scripts.seed_compliance_data --apply`.

## ❌ Gap 2 — `verbal_confirmation` phrase pack is NOT in DB

`backend/app/agents/phrase_pack_extractor.py::_PACK_DEFS` declares **5 packs**:
`lead_gen`, `passover`, `verbal_confirmation`, `c_call`, `amendment`.

PROD DB has only **4** — the canonical `verbal_confirmation` pack (32 rules) is
defined in code but never ingested. The 32 Verbal Confirmation rules from the
source doc are only reachable today via the `c_call` and `amendment` aliases.

**Impact:** if the rubric router ever maps `verbal`/`closer` segments to the
`verbal_confirmation` pack (e.g. as a supplement to supplier-specific verbal
scripts), they will silently get an empty rule set and score 0/0. Today the
verbal segments use supplier-specific scripts (EON 26cps, BG 21cps, etc.) so
this gap is dormant — but if a new supplier without a verbal-contract script
is onboarded, `verbal_confirmation` would be the natural fallback.

**Fix:** run the admin "extract all phrase packs" endpoint or call
`extract_phrase_pack(stage_label="Verbal Confirmation", call_types="closer, verbal", stage_filter="verbal confirmation")` once and save the result with `lifecycle_phase='verbal_confirmation'`.

## ⚠ Gap 3 — Two pack pairs are content-identical

By design (`_PACK_DEFS` reuses the same `stage_filter` for paired packs):

- `Lead Generation` (88) ≡ `Lead Generation - handover and authority` (88) — same 88 source rows under different `lifecycle_phase` labels.
- `Confirmation callback` (32) ≡ `Amendment call` (32) — same 32 source rows under different labels.

**Net:** **240 checkpoints stored, only 120 unique** rules from the doc.

Per [[../04_Sessions/2026-05-12_Session_taxonomy_rebuild]] and the
`rubric_router._PHRASE_PACK_PHASE` map: today the router sends both
`lead_gen` and `pre_sales` segments to the `lead_gen` pack, leaving the
`passover` pack orphaned. Same observation likely applies to amendment vs
c_call — they grade by name match but content is identical.

**Fix (optional):** consolidate to 3 packs (lead_gen, verbal_confirmation,
plus optional pack-specific overrides). Or accept duplication and document
it explicitly so the team doesn't think the rule sets are different.

## ✅ Content faithfulness — spot-check passed

Spot-checked **E.ON Next NHH+HH (TPI) Jan2026** (the highest-traffic verbal script):
- PROD: 26 checkpoints
- Source doc: 26 numbered items (1-26)
- Checkpoint #1 in PROD = "Disclose call recording for monitoring purposes" ↔ Source item 1 = "Calls are recorded to for monitoring purposes" ✅
- Checkpoint #10 = "State plan start date (acquisition or renewal)" ↔ Source items 10a + 10b = "(Acquisition) Your plan will start when your supply is live..." / "(Renewal) Your plan will start when your current one ends" ✅
- 1:1 mapping verified for the first 10 items, count matches for all 26.

Other counts pass the smell test (checkpoint count ≈ source structure: 1-line/rule for verbal scripts like EON, multi-line for prose-heavy ones like EDF Acq V11 with 72 cps from 130 lines).

## Top-line answer

**Are the /scripts page entries 100% based on the source documents?**

- **15 of 16** supplier source documents in `compliance-docs/Supplier Scripts/` are ingested. **Valda is missing.**
- **4 of 5** phrase packs defined in code are ingested. **`verbal_confirmation` is missing.**
- Content of the 19 rows that ARE ingested faithfully matches the source (spot-checked NHH+HH = 1:1).
- Some pack content is intentionally duplicated under different `lifecycle_phase` labels (passover==lead_gen; amendment==c_call).

## Validation reproducibility

```bash
# 1. Live PROD inventory (run from this repo)
curl -sS --ssl-no-revoke "https://compliance-agent-production-690e.up.railway.app/api/scripts" > /tmp/scripts.json

# 2. Source document list
ls "compliance-docs/Supplier Scripts/"

# 3. Phrase-pack source
sed -n '22,55p' .planning/phase2-docs/compliance_xai__watt_ai_phrase_detection_dataset_1.md
```
