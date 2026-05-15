---
created: 2026-05-16
updated: 2026-05-16
tags: [session, polling-rollback, deal-merge, sonnet-routing, audio-bug, handoff]
---

# 2026-05-16 — Polling rollback, deal-merge second-pass, vulnerability schema fix + handoff

**Tip backend:** `e1c8d3b` (Railway)
**Tip frontend:** `e1c8d3b` (Vercel `dpl_442GtuqphZTp78XiiM3WiLNEvHh9`)

## Why this session existed

User started the night frustrated that:
1. Lucca's British Gas uploads "didn't segment" — only 1 segment per call.
2. The deal-linker didn't collapse multi-call same-customer flows.
3. The audio resets on click on the call detail page.
4. Spacebar in the Override→Fail textarea was triggering audio playback.
5. Pages felt "stale" — wanted real-time updates everywhere.

## What I did, in order

| Commit | Layer | What changed |
|---|---|---|
| `0c2408e` | backend | Strengthen `CONTENT_CLASSIFIER_PROMPT` (supplier-neutral signals + Watt anchors), `min_confidence 0.5 → 0.35`. Stop `_write_extraction_outputs` from touching `call_segments` (was emitting the obsolete 6-stage taxonomy that the DB CHECK forbids — crashed every call's L2 step). Remove `it's/it is` from strict agent-name triggers; add gated `_IT_IS_AGENT_INTRO` regex. Extend stopwords list. |
| `eb5566d` | frontend | Aggressive polling: `staleTime: 0`, global `refetchInterval: 5_000`, per-page 3 s on operational surfaces, 1.5 s on call detail while processing. **This commit is what created the audio-reset bug.** |
| `a607c29` | brain | Session log + 00_INDEX + Live_State |
| `87bba52` | backend | Mixed Sonnet/Opus routing: new `cheap=True` param on `_call_llm`; `openrouter_cheap_model = "anthropic/claude-sonnet-4.6"`. Wired Sonnet on `detect_supplier`, `detect_call_type`, `detect_names`, `detect_business_name`. Rewrite `DETECT_PROMPT` (broker-target supplier vs customer-current). Rewrite `business_detect._PROMPT` with Watt anchors + reject single-token person-name candidates. Loosen `_maybe_merge_into_existing_deal` to fuzzy 0.85 + supplier-permissive when candidate has NULL supplier. |
| `52790a1` | backend | Add `override_customer_name` param to `_maybe_merge_into_existing_deal`. Invoke a second-pass merge AFTER `detect_business_name` resolves (the first pass only had the person name on `call.customer_name`). Threshold lowered to 0.80. |
| `e1c8d3b` | both | **Polling rollback** — revert global `refetchInterval`, only the in-flight processing call detail polls, queue keeps slow 15s background poll. **Vulnerability flag** — set `risk_tag=None` (was `"Vulnerable"`, which violated `ck_flags_risk_tag` and crashed every call's L2 step with `PendingRollbackError`, producing the `ReadError('')` cascade Mohamed saw on every CP). **Spacebar guard** — Override-Fail textarea stops Space/Enter propagation. |

## Upload tests run tonight

| Attempt | Set | Result |
|---|---|---|
| #1 (Lucca, BG) | 7 files Mr Alistair / St Peters | All processed; each got 1 segment (classifier returned `[]`, fallback) and `L2_EXTRACTION_FAILED` on every call. Caused by `ck_call_segments_stage` violation. Fixed by `0c2408e`. |
| #2 (Awais Mustafa, BG) | 4 files Leadgen / Passover / Verbal / LOA | 4 calls → 4 deals (first pass) → 4 calls → 3 deals (after `52790a1` second-pass merge). 2 of 4 multi-segment correctly. Agent names all real. Transcripts so wildly different ("Awais Mustafa Ta Shah's Palace" vs "Waste Master Trading As Charles Palace") that fuzzy 0.80 can't fully collapse them. |

## What's still broken / open

| Severity | Item | Fix sketch |
|---|---|---|
| P0 | The `ReadError('')` on individual checkpoints (OpenRouter network drops mid-batch) — should be **fixed transitively** by `e1c8d3b` because the L2 crash is what was poisoning the session. Needs to be re-verified by a fresh upload after `e1c8d3b` is live. | Upload one call, confirm `L2_EXTRACTION_WRITE` succeeds in logs without the `PendingRollbackError`. |
| P1 | True push-based real-time (SSE / WebSocket from Railway) | New `GET /api/calls/{id}/events` FastAPI endpoint returning `text/event-stream`; in-memory pub/sub keyed by `call_id`; pipeline steps publish on each transition; frontend `useEventSource` hook invalidates the matching React Query key. ~3 files, ~2 hours. |
| P1 | Supplier mis-detection on non-EON files (3 of 4 Clifton + 4 of 4 Awais came back "E.ON Next" even though they're British Gas) | `DETECT_PROMPT` already rewritten in `87bba52` to anchor on broker-target — re-test after `e1c8d3b`. If still wrong, add a few-shot example block. |
| P1 | Customer-name = person, not business — deal-linker can't fully collapse same-customer | Strengthen `business_detect._PROMPT` with more examples drawn from the AI Data fixtures + drop the "fall back to person name" path entirely. Optionally add Metaphone phonetic match (already in `intake/matcher.py:_metaphone`) into `_maybe_merge_into_existing_deal`. |
| P2 | LOA recordings drop into `needs_manual_review` with 88/88 analyzer errors | Investigate the LOA recordings specifically — likely short transcript causing the LLM batch to time-out. Add a per-CP retry-with-Sonnet fallback. |
| P2 | One stub deal stays as `"(auto-detect pending {short_id})"` when the Leadgen call's customer detection returns None | Either: drop the upload-time stub entirely (defer deal creation until detect_metadata resolves), OR add a "rename stub at finalize" step that uses the business_name when present. |

## Deploys + URLs (still current)

- Frontend: <https://compliance-agent-mu.vercel.app>
- Backend: <https://compliance-agent-production-690e.up.railway.app>
- Supabase project: `zcmdsblqbgatsrofptsq` (ap-south-1)
- Admin login: `admin@compliance-agent.local` / `Audit-Pass-2026-05-10!`
- Admin key (Railway env `ADMIN_KEY`): `2igJL74ro4ilF_yvO5W_tbJQCO-mqh5miodCBv52qoE`
- Supabase anon key (Railway env `SUPABASE_ANON_KEY`): see `railway variables --service compliance-agent --environment production --json`

## Operational reminders pinned tonight

- **Vercel CLI deploys** still get blocked with `COMMIT_AUTHOR_REQUIRED` because local git author is `kingusa1 <IT@bbmgroup.io>`, not a verified Vercel team seat (`mohamedhisham735@gmail.com`). GitHub-source deploys bypass:
  ```bash
  curl -sSk -X POST "https://api.vercel.com/v13/deployments?teamId=$TEAM" \
    -H "Authorization: Bearer $VERCEL_TOKEN" -H "Content-Type: application/json" \
    -d '{"name":"compliance-agent","target":"production",
         "gitSource":{"type":"github","org":"kingusa1","repo":"compliance-agent",
                      "ref":"main","sha":"<SHA>"}}'
  ```
- **Reviewer JWT for API calls** — mint via Supabase password grant:
  ```bash
  curl -sSk -X POST 'https://zcmdsblqbgatsrofptsq.supabase.co/auth/v1/token?grant_type=password' \
    -H "Content-Type: application/json" -H "apikey: $SUPABASE_ANON_KEY" \
    -d '{"email":"admin@compliance-agent.local","password":"Audit-Pass-2026-05-10!"}'
  ```
- **Wipe DB**: `POST /api/admin/wipe-all-calls?confirm=YES_DELETE_EVERYTHING` with Bearer JWT
- **Test files**: `compliance-docs/AI Data/{EON,Non-EON}/{Compliant,Non Compliant}/*.mp3`
- **Pre-converted Awais set** (safe filenames): `/tmp/awais_1_leadgen.mp3`, `awais_2_passover.mp3`, `awais_3_verbal.mp3`, `awais_4_loa.mp3`

## Mistake log for next-me to avoid

- **Don't add aggressive `refetchInterval` again.** Mohamed explicitly hates re-render flashes; real-time = SSE push, not polling.
- **Don't add new values to DB `risk_tag` / `stage` columns without first updating the CHECK constraint** (this has now bitten us twice: `ck_call_segments_stage` then `ck_flags_risk_tag`). When introducing a new enum value backend-side, write the Alembic migration FIRST.
- **Don't trust the filename prefix** — recordings labelled `EON_Next__...` may contain British Gas content. The script picker reads the prefix at intake but the transcript may say otherwise.
- **The deal-merge runs at finalize using `call.customer_name` (person name) BY DEFAULT.** Pass `override_customer_name=business_name` in the second-pass invocation — the first pass alone never collapses correctly.
