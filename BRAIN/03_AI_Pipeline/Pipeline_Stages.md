---
created: 2026-05-10
updated: 2026-05-10
tags: [ai, pipeline]
---

# AI Pipeline — 6 steps per upload

Source: `backend/app/pipeline.py` (orchestration) + `backend/app/workflows/process_call.py` (Inngest variant).

## Step 1 — `_step_download_audio`
Pulls audio from Supabase Storage (`compliance-audio` bucket) into a tmp file, OR reuses an already-on-disk file from a direct upload.

## Step 2 — `_step_transcribe`
**Parallel** call to up to 5 STT engines via `asyncio.gather`:
- **Deepgram Nova-3** (primary, en-GB, EU region) — speaker-diarised + sentiment + intents + topics + summary in one shot
- AssemblyAI (optional fallback)
- Groq Whisper (optional fallback)
- Cohere (optional fallback)
- Gemini (optional fallback)

Persisted: `call.transcript`, `call.word_data` (JSON), `call.deepgram_metadata` (full Deepgram response).

`call.duration_seconds` populated from Deepgram's container probe (or last-word `end` timestamp as fallback).

## Step 3 — `_step_detect_metadata`
1. **`detect_names(transcript)`** — Opus 4.7 returns `(agent_name, customer_name)`. Hardened prompt + collision guard (if both = same, clear agent).
2. **`detect_supplier(transcript)`** — Opus 4.7 returns supplier free-text.
3. `canonicalize_supplier()` → canonical enum + label.
4. **Sibling-supplier inheritance** — if the LLM said "Unknown":
   - Same-deal pass: any other call on this deal_id with supplier set → inherit.
   - Cross-deal pass: any other call sharing the same human customer_name (bidirectional substring + token-overlap) with supplier set → inherit.
5. **Script auto-match** — see [[02_Domain/Scripts]] for the matching rules.
6. **Stub merge / rename**:
   - If `detect_business_name` returns a name AND a fuzzy or human-name match finds an existing customer → re-point `call.deal_id` to that customer's open deal, delete orphan stub.
   - If no match AND deal still has the auto-detect-pending stub label → rename in place + re-slug the customer.
7. Filename rename to `<Supplier>__<Script>__<original>.mp3` for traceability.
8. **MPAN/MPRN/deal_value** propagation from extracted entities → `CustomerDeal`.

## Step 4 — `_step_analyze_checkpoints`
Idempotent (deletes prior `CallCheckpoint` rows first).

If `call.script_id` is set AND the script has non-empty `checkpoints`:
- Run `analyze_all_checkpoints()` (`backend/app/checkpoint_analyzer.py`) — batches checkpoints, calls Opus 4.7 with cacheable system prompt, validates evidence against transcript via fuzzy match, populates AI category + fix_required + ai_rejection_reason + ai_narrative_notes.

Else (no script OR empty checkpoints):
- Fall through to `analyze_compliance_v1()` — universal Third-Party Disclosure rule (3 checkpoints).

## Step 5 — `_step_score`
Derives `call.score` (X/Y), `call.compliant` (bool), `call.status` (`completed` / `needs_manual_review`), `call.reason` (one-line summary). Score < threshold → spawns a `Rejection` row via `_maybe_create_rejection`.

## Step 6 — `_step_finalize`
1. Detect segments (talk/silence boundaries) — `app/extraction/segments.py`
2. Extract entities (MPAN, MPRN, postcode, dates, £ values) — `app/extraction/entities.py`
3. Derive flags (Mis-selling, Ombudsman, Vulnerable) — `app/extraction/flags.py`
4. Vulnerability detector (LLM) — `app/extraction/vulnerability.py`
5. Pricing-mismatch flags (when feature flag on) — `app/extraction/flags.py:derive_pricing_mismatch_flags`
6. **🤖 Quality Agent auto-merge** — `app/quality_agent.py:auto_resolve_for_call(call_id, db)`
   - Buckets sibling calls by overlapping human customer_name
   - If ≥2 calls in bucket → ask Opus 4.7 for canonical identity
   - If `confidence ≥ 0.7` AND `stitch == "merge_all"` → re-point sibling deals, rename customer canonically, fix agent name, fill missing supplier
   - See [[03_AI_Pipeline/Quality_Agent]] for the system prompt.

## Visualisation in UI
The reviewer sees a `PipelineTimeline` component on every call detail page (`frontend-v3/src/components/design/PipelineTimeline.tsx`) with 5 stages:
1. Deepgram Nova-3 transcription (✓ if transcript present)
2. Speaker labels — Agent / Customer (✓ if names populated)
3. Supplier auto-detection (✓ if supplier non-Unknown)
4. Script auto-match (✓ if script_id populated)
5. Opus 4.7 checkpoint analysis (✓ if score populated)

Each stage has a status icon (green / amber / gray), the AI's output, and a one-line "why" tooltip.

See [[03_AI_Pipeline/Speaker_Detection]] · [[03_AI_Pipeline/Quality_Agent]] · [[03_AI_Pipeline/Future_Agents]].
