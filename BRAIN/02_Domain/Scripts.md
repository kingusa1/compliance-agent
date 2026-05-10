---
created: 2026-05-10
updated: 2026-05-10
tags: [domain, scripts]
---

# Supplier scripts

## The 15 active scripts (seeded)
Stored in `Script` table. Source: `backend/app/watt_compliance/supplier_seed.py` `CATALOGUE`.

| Supplier | Script Name | Version | Phase |
|---|---|---|---|
| BGL | BGL Broker Acquisition Script | V7 | Acquisition |
| BGL | BGL Acquisition Script (legacy) | V6 | Acquisition (deprecated) |
| British Gas | British Gas Broker Acquisition Script | V0.2 | Acquisition |
| British Gas | British Gas Broker Renewal/Deemed Script | V03 | Renewal |
| EDF | EDF TPI Fixed-for-Business Acquisition Script | V11 | Acquisition |
| EDF | EDF Pre-amble Script | v1 | Preamble |
| **EON (E.ON Next)** | **E.ON Next NHH+HH Verbal Contract Script (TPI)** | **Jan2026** | **Acquisition** |
| EON | E.ON Next Gas Verbal Contract Script (TPI) | Jan2026 | Acquisition |
| EON | E.ON Next Gas Verbal Contract Script | undated | Acquisition (deprecated) |
| EON | E.ON Next Elec Verbal Contract Script | undated | Acquisition (deprecated) |
| **EON** | **E.ON TPI Verbal LOA Script** | V2 | LOA (sentence on standalone LOA path even though E.ON is 2-stage — historical) |
| Pozitive | Pozitive Verbal Contract Script (PE) | PE | Acquisition |
| Scottish Power | Scottish Power Acquisition Script (TPI) | Oct2024 | Acquisition |
| Scottish Power | Scottish Power Renewal Script (TPI) | Oct2024 | Renewal |
| Scottish Power | Scottish Power Multisite Acquisition Script | Oct2024-multisite | Acquisition |

## Script auto-match logic
`backend/app/pipeline.py:_step_detect_metadata` ~lines 380-460:

1. If user passed `script_id` on upload → use it (manual override).
2. Else: `detect_supplier(transcript)` returns LLM-supplier guess.
3. `canonicalize_supplier(detected)` → enum.
4. Pull all `Script.active == True` rows.
5. Filter Python-side: `canonicalize_supplier(script.supplier_name) == canon`.
6. If the call has a known `call_type`, prefer scripts whose `lifecycle_phase` matches (or is NULL for back-compat).
7. If multiple match → `detect_script_variant()` — second LLM call picks the closest variant.
8. Persist `call.script_id` and re-set `call.detected_supplier` to the canonical label (e.g. "E.ON Next", not "EON").

## Script content storage
`Script.checkpoints` is a JSON string. Each checkpoint has:
```json
{
  "section": 1,
  "name": "The agent explicitly states the company is a third party",
  "required": "We're a third-party broker, not an energy supplier",
  "key_phrases": ["third party", "broker", "intermediary"],
  "customer_response_required": false,
  "strictness": "mandatory",   // verbatim | mandatory | customer_yes
  "line_number": 14
}
```

## ⚠️ Gotcha
Multiple seed scripts in the live DB have `checkpoints: "[]"` — empty. When matched, the analyzer falls through to V1 third-party-disclosure prompt (`backend/app/analysis.py` `V1_PROMPT`) so the call still scores 0/3 or 2/3 on the universal third-party rule. Fix added 2026-05-10: `pipeline.py:_step_analyze_checkpoints` detects empty `checkpoints_def` and routes to V1 fallback instead of returning 0/0.

To populate scripts properly, run `backend/scripts/seed_compliance_data.py --apply` (requires phase2-docs/ markdown extracts present).

See [[02_Domain/Lifecycle]] for which script applies in which phase.
See [[03_AI_Pipeline/Pipeline_Stages]] for where script-match sits in the flow.
