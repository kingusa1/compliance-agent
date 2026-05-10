---
created: 2026-05-10
updated: 2026-05-10
tags: [ai, agents, quality, headline]
---

# Quality AI Agent (Opus 4.7) — the headline feature

## What it solves
Per-call detection gets us 80%. The remaining 20% is **cross-call ambiguity** that no single-call prompt can resolve:
- LLM extracts slightly different business names from sibling calls of the same customer (`The Church`, `Evangelical Church`, `St. Peter's Benfleet Church`)
- Customer ↔ agent name confusion when both have first names mentioned
- Closer / LOA calls don't say the supplier explicitly because the customer already knows
- Three uploads of one customer's calls land on three different stub deals because they were uploaded before any could fully process

The Quality Agent reads **all candidate sibling calls together** and produces ONE canonical identity record.

## File
`backend/app/quality_agent.py`

## System prompt (cacheable)
The agent is told:
- This is a UK third-party-intermediary energy broker (Watt) auditing recorded sales calls
- 6 known suppliers (BGL, BG, EDF, E.ON Next, Pozitive, Scottish Power)
- Rules for canonical_customer_name (most specific business name wins), customer_person, agent_name (broker, NEVER customer), supplier (cross-validate), call_classifications, stitch verdict, confidence

Strict JSON output, vocabulary-validated. Anything outside the schema is logged + ignored.

## Output schema
```json
{
  "canonical_customer_name": "Dorothy's Evangelical Church",
  "customer_person": "Christopher Neil Banks",
  "agent_name": "Afak",
  "supplier": "E.ON Next",
  "call_classifications": {
    "<call_id_1>": "lead_gen",
    "<call_id_2>": "closer",
    "<call_id_3>": "loa"
  },
  "stitch": "merge_all",
  "stitch_reason": "All three calls reference Christopher Neil Banks, Evangelical Church, same postcode, E.ON Next contract with agent Afak.",
  "confidence": 0.92
}
```

## Where it runs
1. **Auto-runs after every upload** — `pipeline.py:process_call` finalize step calls `auto_resolve_for_call(call_id, db)`. Only fires if the new call has ≥1 sibling candidate (cost guard). Verdict is only applied at confidence ≥0.7 with stitch=merge_all (safety guard).
2. **Admin endpoint** — `POST /api/admin/quality-resolve` runs the agent across ALL completed calls, buckets by overlapping name, applies. Idempotent.

## Helpers (importable)
- `find_sibling_candidates(call_id, db) → bucket` — gathers candidate sibling calls
- `resolve_identity(calls_dicts) → verdict` — the actual Opus call
- `apply_verdict_to_db(bucket, verdict, db) → change_record` — applies the merge
- `auto_resolve_for_call(call_id, db) → change_record` — one-shot for the pipeline

## Verified live (2026-05-10)
3 Evangelical Church calls (`The Church`, `Evangelical Church`, `St. Peter's Benfleet Church`) → merged into:
- Customer: **`Dorothy's Evangelical Church`** (slug `dorothy's evangelical church`)
- 1 deal · 3 calls
- Supplier `E.ON Next`
- Agent: `Afak` (correctly identified — heuristics had been mis-tagging him as customer)
- Confidence: `0.92`

Test result lives in [[05_State/Test_Calls]].

## Roadmap
[[03_AI_Pipeline/Future_Agents]] — Call-Type Classifier, Decision-Maker Confirmer, Verdict Reviewer, Data Enricher, Multi-agent Orchestrator.

## Why Opus 4.7
- Cross-call reasoning over 4-12k tokens of transcript needs a strong reasoner
- Strict JSON output reliability vs. cheaper models
- Already on `OPENROUTER_MODEL=anthropic/claude-opus-4.7` for the rest of the pipeline; reuses prompt cache
