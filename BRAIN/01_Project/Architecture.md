---
created: 2026-05-10
updated: 2026-05-10
tags: [architecture]
---

# Architecture

## High-level
```
                ┌─ Vercel (Next.js 16, lhr1) ─────────────────────────┐
                │  compliance-agent-mu.vercel.app                     │
                │   /dashboard /queue /tracker /customers …           │
                └────────────────────────────┬────────────────────────┘
                                             │ Bearer JWT
                                             ▼
                ┌─ Railway (FastAPI, eu-west) ────────────────────────┐
                │  compliance-agent-production-690e.up.railway.app    │
                │   /api/calls /api/customers /api/deals …            │
                │   /api/admin/quality-resolve  (Opus 4.7 agent)      │
                └─────┬───────────┬───────────┬───────────┬───────────┘
                      │           │           │           │
                      ▼           ▼           ▼           ▼
                Supabase    Deepgram    OpenRouter    Inngest
                (Postgres,   (Nova-3,    (Opus 4.7    (durable
                 Storage,     en-GB,      anthropic/   workflows,
                 Auth)        EU region)  claude-       optional)
                                          opus-4.7)
```

## Data model (key tables)
- `Call` — one upload. Has transcript, word_data, deepgram_metadata, score, agent_name, customer_name, detected_supplier, deal_id, script_id, call_type, status.
- `CustomerDeal` — groups multiple calls of the same supply contract. Has supplier, customer_name (BUSINESS), mpan_or_mprn, deal_value_gbp, status.
- `Customer` — one business. Has slug (URL-safe), legal_name, deals.
- `Script` — one supplier verbal-contract / LOA script. Has supplier_name, script_name, version, mode, checkpoints (JSON), lifecycle_phase, active.
- `CallCheckpoint` — per-checkpoint verdict for one call.
- `Rejection` — auto-created when score < threshold; tracks Active → Fixed/Dead lifecycle.

See [[02_Domain/Watt_Compliance]] for the rejection taxonomy.

## Pipeline (per upload)
[[03_AI_Pipeline/Pipeline_Stages]] documents this in detail. Six steps:
1. download_audio (or read from Storage)
2. transcribe (Deepgram Nova-3 + 4 fallback engines via `asyncio.gather`)
3. detect_metadata (names, supplier, script variant, filename rename)
4. analyze_checkpoints (Opus 4.7 batched per checkpoint)
5. score (derive call.score / compliant / status / reason)
6. finalize (segments, flags, entities, **Quality Agent auto-merge**)

## Workflows (Inngest, optional)
`USE_INNGEST_PIPELINE` env flag toggles between in-process asyncio (default) and durable Inngest workflows. Both paths converge on the same step functions in `pipeline.py`.

## Field-source provenance
Every Call/Deal/Customer/Rejection field has a `field_sources` JSON column. Sources:
- `user` (manual edit) — wins on conflict
- `ai` (LLM extraction)
- `human-override` (reviewer accepted)
- `auto` (heuristic fallback)
- `inherited` (cross-call propagation, e.g. supplier from sibling)

`can_overwrite()` in `app/field_sources.py` enforces precedence so AI never clobbers a user edit.
