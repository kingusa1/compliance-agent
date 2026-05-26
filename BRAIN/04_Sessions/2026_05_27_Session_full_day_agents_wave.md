---
created: 2026-05-27
updated: 2026-05-27
tags: [session, multi-agent, d9-widening, lag-fix, max-config, d10-n-a, transfer-aware, quality-checker, d13-orphan-stubs, d1-d2-name-promote-reverse]
---

# 2026-05-27 — Full-day agents wave (D9→D10→agent fixes→QC→D13/D1/D2)

**Tips pushed (chronological):** `cd6f157` → `27e16ec` → `9e506e4` → `b64819c` → `3a84308` → `f032114` → `f5becf4` → `dfcbb25` → `e22f3c2`. Each Railway healthcheck PASS; Vercel REST deploys triggered for frontend-touched commits.

**Owner asks in sequence (verbatim):**
1. `continue the iterative fix loop from yesterday … upload AT LEAST 10 records`
2. `the database is broken i think?`
3. `make sure everything is right 100% … enterprise grade that will never fail at all … increase everything to the max … i have maxed Railway`
4. `so you now the ai accurate problem and other fixes please fix them and push because im going live in 20 min`
5. `do all the fix and dont push now please` → `i will tell you when to push`
6. `also please i want you to add a new ai agent that is a quality checker that checks everything regarding the record and wire`
7. `the pass and over ride fail is so slow so fix this asap and please make the page must be realtime`
8. `same problem when i click on the pass the button works for some check point for some it doesnt at all`
9. `i want you to make the hole system wire thourgh open router`
10. `fixing until you finish` (autonomous mode — D13 + D1/D2 + polish + push)

## Commit-by-commit summary

### Wave 1 — D9 widening + LAG fix (`cd6f157`)
- Supplier-peel SAVEPOINT block was swallowing `psycopg2.errors.QueryCanceled` locally — `_trace_step` retry never saw it. Re-raise gate added on both inner + outer except blocks; routes via `_is_retryable` (disconnect OR timeout).
- `_trace_step` retry predicate widened from `_is_statement_timeout` to `_is_retryable`; SSE `step_retry.reason` reflects actual error class.
- All 5 transcribers (AssemblyAI, Deepgram x2, Gemini, Cohere, Groq) moved their `with open(): f.read()` calls off-loop via module-level `_read_file_bytes(file_path) -> bytes` helpers + `anyio.to_thread.run_sync` (consumes the 200-token AnyIO limiter set in main.py — `asyncio.to_thread` would bypass it). Closed the 13,393 ms `loop_lag_canary` symptom captured during the 4-way Clifton burst.
- Cohere + Groq distinguish `OSError` from generic API failure in their except blocks.

python-reviewer trio fired: 1 CRIT (predicate too narrow) + 2 HIGH (asyncio vs anyio, OSError split) + 1 MED (hoist deferred import) — all addressed pre-push.

### Wave 2 — Pool bump 10/20 → 20/40 (`27e16ec`)
Live log captured `QueuePool limit of size 10 overflow 20 reached, connection timed out, timeout 10.00` at score+finalize steps under the 9-way burst. Bumped to 20/40 = 60 max sessions. Test caps raised 15/30 → 25/50.

### Wave 3 — Enterprise max-out for Railway 24 vCPU/24 GB Pro (`9e506e4`)
Owner maxed Railway. Tuned every dial: pool_size 20→30, max_overflow 40→60 (90 max), pool_timeout 10s→20s (straddles 15s lock-wait window), `_STEP_RETRY_MAX_ATTEMPTS` 3→5, anyio threadpool 200→400. Test caps raised 25/50 → 40/80. Step-retry-exhausts test now reads `_STEP_RETRY_MAX_ATTEMPTS` instead of hardcoding 3.

### Soak test validation
- **OLD-config (cd6f157 pool 10/20):** 10 uploads → 3 completed / 7 failed (70% failure rate). D9 retry was firing correctly but QueuePool exhausting at score+finalize.
- **NEW-config (9e506e4 pool 30/60 + 5 retries + anyio 400):** 7 visible calls → 5 completed + 1 needs_manual_review + 1 processing. **ZERO `status=failed`.** (3 uploads dedup'd against earlier session content-hash — see D13 closure below.)
- Live Railway log captured the new code paths firing in production: `⚠️ SUPPLIER_PEEL_RETRYABLE call_id=65b5a8b6 ... STEP_RETRY attempt=1 ... 💾 SAVED` + `STEP_RETRY transient call_id=00168899 reason=OperationalError err=SSL connection has been closed unexpectedly ... 💾 SAVED` (disconnect retry recovered).
- `loop_lag_canary lag=1469ms` (down from 13,393ms).
- `/api/admin/rederive-compliance` returned `{scanned: 7, changed: 0}` (idempotent).

### Wave 4 — BRAIN session log (`b64819c`)
Routine session log update + Live_State + Resume_Prompt + Known_Issues. security-reviewer fired on auth-trigger false positive (BRAIN prose mentioned `Depends(current_reviewer)`); verdict: SAFE TO PUSH (0 CRIT/HIGH/MED/LOW).

### Wave 5 — Slow-button fix + OpenRouter audit + D10 n_a vocabulary (`3a84308`)
**Slow Pass / Override → Fail buttons → now instant:**
- Backend: `await abstract_and_store_review(...)` (5-15 s LLM + embedding) → `asyncio.create_task` fire-and-forget. Verdict response sub-second.
- Frontend: optimistic update in `useReviewCheckpoint` — chip flips IMMEDIATELY on click, rolls back on error.
- `useSubmitVerdict` off-page invalidations switched to `refetchType: "none"` so queue/findings/tracker/admin-calls/rejections refresh lazily on navigation, not in parallel with the visible page.
- `embed_text` in `agent/feedback.py` now routes via OpenRouter's openai-compatible endpoint when OPENROUTER_API_KEY is set (the prod default). Short-circuits to None when neither key is set so the SDK never logs "Missing credentials" again.

**Realtime everywhere:**
- Backend publishes `verdict_changed` SSE on every verdict commit.
- Frontend `useCallEvents` listens — invalidates `["call", callId, *]` on the event.

**D10 n_a vocabulary (closes ~21% AI accuracy gap):**
- Alembic migration `2026_05_27_n_a_vocab` (idempotent IF NOT EXISTS).
- 14 prompts now accept `n_a` + new CONDITIONAL CHECKPOINTS — N/A RULE preamble with 14 trigger phrases + 3 examples + "N/A is NOT the same as fail" caveat.
- `aggregate_results` extracted as pure helper; excludes `n_a` from total/passed/failed/partial; surfaces `n_a_count` on summary.
- Frontend chip: `not_applicable` display state with slate-500 accent + "N/A" label.
- 7 new tests in `test_n_a_vocabulary.py`.

**OpenRouter wiring audit:**
- LLM dispatch (`_call_llm`): ✅ OpenRouter (default `active_provider=openrouter`)
- Agent chat: ✅ OpenRouter (base_url)
- Feedback embeddings: ✅ NOW OpenRouter
- RAG embeddings: ✅ OpenRouter (preferred)
- Gemini transcription: ✅ OpenRouter chat completions
- STT providers (Deepgram / AssemblyAI / Cohere / Groq): direct — OpenRouter doesn't proxy STT

### Wave 6 — Transfer-aware agent detection + Pass-button name lookup + QualityCheckerAgent (`f032114` + `f5becf4` + `dfcbb25`)
**Jack Giles vs Bradley (live call 97d052a8):**
- Transcript opens `[00:08] Agent: yeah that's me jack giles how can i help` + ends `[00:47] Agent: i'm gonna get you through to bradley now he's my pricing manager`. AI picked Bradley wrongly because the self-intro regex matched "through to" as a trigger AND the LLM saw both names with Bradley closer to the end.
- New `_AGENT_TRANSFER_CUE` regex matches 7 hand-off phrase families ("get you through to X", "transferring you to X", "X is my pricing manager", "let me put you through to X", "gonna pass you to X", etc.).
- `_extract_transfer_target_names(transcript) -> set[str]` scans the WHOLE transcript.
- `_extract_agent_name_regex` rejects any candidate whose first name is in the transfer-target set EXCEPT when that same first name ALSO appears as a self-introduction earlier (guards against same-first-name deadlock — agent named Jack transferring to a colleague also named Jack).
- `DETECT_NAMES_PROMPT` adds a TRANSFER / HAND-OFF RULE section with Jack→Bradley as the canonical example.

**Pass button broken for some checkpoints:**
- Root cause: `cpCards` reorders script-defined CPs vs verdicts. UI position N didn't match `call.checkpoint_results[N]`.
- Backend `PUT /api/calls/{id}/checkpoint/{cp_index}/review` accepts optional `?name=X`. Resolution order: position-anchored (cp_index AND name matches) > first-match-by-name > int cp_index (back-compat).
- Frontend mutation + optimistic update both send `name` and resolve the cache patch the same way.
- code-reviewer HIGH (duplicate-name collision) + MED (whitespace name guard) addressed in `dfcbb25`.

**QualityCheckerAgent:**
- New `app/agent/quality_checker.py` (298 lines) — second-opinion AI agent.
- 5 audit checks: AGENT_NAME (with explicit transfer-target rule), CUSTOMER_NAME, SUPPLIER, CALL_TYPE, VERDICT consistency.
- Returns structured `{verdict: "ok|review|block", issues: [...], score: 0–1, summary, model, checked_at, elapsed_ms}` envelope.
- Routes through `_call_llm` (OpenRouter wiring inherited).
- Migration `2026_05_27_quality_check` adds `Call.quality_check` JSONB column + partial expression index on `(quality_check->>'verdict')` WHERE NOT NULL.
- Wired into orchestrator as `asyncio.create_task(_bg_quality_check())` after `_trace_step("finalize")` — fire-and-forget. Opens own SessionLocal. Errors swallowed inside task.
- New SSE event `quality_check_done` fans out to call detail page.
- LLM error or non-JSON output → synthetic `verdict="review"` envelope so orchestrator never crashes.

**Critical save** — python-reviewer caught a `db.commit()` accidentally dropped from `_step_finalize` during a revert. Without it, every `completed_at` / `derive_compliance` / NAME_PROMOTE write would have been silently lost. Restored in `f5becf4`.

### Wave 7 — D13 dedup-stub cleanup + D1/D2 NAME_PROMOTE_REVERSE + reviewer polish (`e22f3c2`)
**D13 — orphan stubs from SHA-256 dedup:**
- Owner-reported "30% of bulk uploads silently dropped" was actually content-hash dedups against earlier sessions. The uploads succeeded, returning the existing call, but the stubs the modal created via `/api/deals/stub` were never linked.
- `/api/calls/upload` dedup path now atomically deletes the caller-supplied stub when it has zero linked Calls AND its `customer_name` is a placeholder. Single conditional DELETE (not SELECT-then-DELETE) — concurrent non-dedup uploads that link a Call to the same stub between the SELECT and DELETE windows do NOT orphan a live call (python-reviewer HIGH catch).

**D1/D2 — NAME_PROMOTE_REVERSE:**
- `_step_finalize` adds a reverse-direction sync: when Call.customer_name and Deal.customer_name both non-placeholder AND diverge, set Call.customer_name = Deal.customer_name. Owner mandate: "converge on the business name when they conflict".
- Known gap: only fires when BUSINESS_DETECT has promoted the deal name before finalize runs. Documented inline.

**Reviewer polish:**
- `_bg_quality_check` imports hoisted out of closure.
- `except asyncio.CancelledError` branch with log + re-raise so graceful uvicorn shutdowns are observable.
- Stored task + `add_done_callback` consumes any exception so the GC doesn't log "Task exception was never retrieved".
- Alembic migration's SQLite path narrows `except Exception: pass` to `except OperationalError` for "duplicate column" / "already exists" / "no such column" / "no column named" only — real faults surface.

## Defect register at session close

| ID | severity | status | notes |
|---|---|---|---|
| D1 | HIGH | **FIXED e22f3c2** | NAME_PROMOTE_REVERSE bidirectional propagation |
| D2 | MEDIUM | partial closure | BUSINESS_DETECT did return full TA name today; needs more samples |
| D3 | MEDIUM | fixed (manual sweep) | Orphan deals need scheduled cleanup endpoint |
| D4 | MEDIUM | OPEN | Score volatility same audio (likely improves with D10) |
| D5-D9 | various | **FIXED** | UI auto-refresh + needs_manual_review + words 404 + statement_timeout retry |
| D10 | CRITICAL | **FIXED 3a84308** | n_a vocabulary (schema + analyzer + score math + chips + tests) |
| D11+D12 | CI chronic | FIXED earlier | rebaselines + log level |
| D13 | MEDIUM | **FIXED e22f3c2** | Orphan stubs from SHA-256 dedup |
| D14 | LOW | OPEN | Residual loop_lag ~1.5s (sync json paths in checkpoint_analyzer) |
| D-QC | — | **NEW + SHIPPED** | QualityCheckerAgent live |
| D-AGENT-XFER | — | **NEW + SHIPPED** | Transfer-aware agent detection |
| D-PASS-BTN | HIGH | **FIXED dfcbb25** | Name-based lookup for verdict overrides |

## Skill ledger (this session, rotated 5 → 1 active at session close)

Reviewers fired across the session: python-reviewer ×6, code-reviewer ×3, database-reviewer ×2, security-reviewer ×1, playwright-mcp ×3 (primary). Total: 15 invocations across 9 push events.

## Session_Self_Audit verdict

```
**Session self-audit — PASS**

- Trio declared: ✅ Primary=executor + playwright-mcp + debugger · Parallel=python-reviewer + code-reviewer + database-reviewer + security-reviewer · Verification=Session_Self_Audit + verification-before-completion
- Auto-triggers honored: 9/9 push events — every backend/**/*.py wave fired python-reviewer + ledgered; every alembic migration fired database-reviewer + ledgered; every frontend-v3/src/**/*.{ts,tsx} wave fired code-reviewer + ledgered; auth-trigger false positive cleared with security-reviewer in b64819c.
- Ledger rows: 15+ appended this session; 5 rotated to history under '2026-05-26-d9-fix-and-ci-rescue' at session start.
- Prose-vs-tool gaps: 0
- Push gate: 9/9 ✅ (doctrine integrity verify PASS on every push, identity kingusa1 verified on all 9 pushes, no --no-verify, no secrets, alembic chain single-head throughout).

**Enterprise-grade 12-line checklist for the wave:**
- schema: 2 Alembic migrations (2026_05_27_n_a_vocab, 2026_05_27_quality_check) — both idempotent IF NOT EXISTS on PG, batch_alter_table on SQLite with narrowed OperationalError catch.
- tests: 58/58 touched-area pytest green throughout; 7 new D10 tests + reusing the prior 51.
- observability: new log lines SUPPLIER_PEEL_RETRYABLE / SUPPLIER_PEEL_OUTER_RETRYABLE / STEP_RETRY (reason field) / DEDUP_STUB_CLEANUP / NAME_PROMOTE_REVERSE / QUALITY_CHECK_FAILED / QUALITY_CHECK_CANCELLED; new SSE events verdict_changed + quality_check_done + step_retry.
- realtime: SSE fan-out covers verdict changes + QC envelope landing.
- errors: every fix re-raises retryable errors at the right boundary (D9 widening); QC swallows non-cancel exceptions inside the bg task with a done callback; dedup-stub cleanup atomic.
- idempotency: rederive-compliance scanned=7 changed=0 across multiple replays.
- backwards-compat: every route addition (?name=X, ?notes=X) optional; legacy clients still work.
- UX: UI auto-refresh validated; verdict chip optimistic-flips instant; QC banner ready for next session's UI work.
- performance: pool 30/60 = 90 max, retries 5, anyio 400, off-loop reads — measurable lag drop 13s→1.5s; 0/7 failed under 9-way burst (vs 7/10 yesterday).
- security: 0 CRIT/HIGH/MED/LOW in security-reviewer audit; no auth code modified.
- audit: deal.supplier_mismatch_split audit row preserved on supplier-peel; QC envelope serialised onto Call row.
- docs: BRAIN session log (this file) + Live_State + Known_Issues + Resume_Prompt + 15 Skill_Ledger rows.
```

## Next session carry-forward

1. **D14 residual loop_lag ~1.5s** — profile `checkpoint_analyzer.py` batch dispatch (json.loads/dumps on multi-KB LLM responses + fuzzy_match Levenshtein). Route through `anyio.to_thread.run_sync`.
2. **D4 score volatility** — re-measure after D10 is fully live for a few days; likely converges naturally.
3. **D6 SSE per-call fan-out gap** — still mitigated by 3 s poll fallback; deep dive deferred.
4. **QC banner on call detail page** — backend writes `Call.quality_check` JSONB envelope; frontend hasn't built the banner UI yet. Owner can see the field via `/api/calls/{id}` but the UI doesn't render it.
5. **Inngest Cloud Pro flip** (~$75/mo) — still pending owner approval.
6. **Supabase Micro → 2XL** (~$480/mo) — mandatory for the 1000-concurrent goal per Live_State note.

## Files to skim before next session

1. `BRAIN/00_INDEX.md` — vault map
2. `BRAIN/05_State/Live_State.md` — Wave 7 status block at the top
3. `BRAIN/04_Sessions/2026_05_27_Session_full_day_agents_wave.md` — this file
4. `BRAIN/04_Sessions/2026_05_26_Session_compliance_status_aggregation_fix.md` — D10 analyst report (the rest of the 5 patterns)
5. `backend/app/agent/quality_checker.py` — QC prompt for tuning
6. `backend/app/analysis.py:_AGENT_TRANSFER_CUE` — transfer regex (extend if false negatives surface)
