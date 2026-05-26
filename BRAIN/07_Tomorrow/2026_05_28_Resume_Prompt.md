---
created: 2026-05-27
updated: 2026-05-27
tags: [resume-prompt, next-session, d10-n-a-vocabulary, d1-d2-business-detect, d13-upload-drops, d14-residual-lag]
---

# 2026-05-28 — Resume prompt for the next session

> Drop the content of the next code block into the next session as the initial user message. It triggers the full read-brain bootstrap and continues the work prioritised by D10 (highest-leverage open defect).

---

## Copy-paste prompt (literal)

```text
/gsd continue from yesterday's session. Read the brain FIRST —
BRAIN/00_INDEX.md, BRAIN/05_State/Live_State.md, the most recent
04_Sessions/2026_05_27_*.md, BRAIN/05_State/Known_Issues.md, and
BRAIN/07_Tomorrow/2026_05_28_Resume_Prompt.md. Then run the session-
start doctrine bootstrap (integrity verify, ledger list-active,
retro queue). After that:

PHASE 1 — D10 n_a vocabulary (highest-leverage open defect)
===========================================================
Per the 2026-05-26 analyst report (in 2026_05_26_Session_compliance_
status_aggregation_fix.md), ~21 % of AI verdicts are clearly wrong;
pattern 1 (n_a vocabulary missing) alone removes ~16 phantom failures
per call. Enterprise-grade implementation:

1. Schema (Alembic, idempotent, IF NOT EXISTS):
   - ALTER TABLE call_checkpoints ADD COLUMN IF NOT EXISTS status
     VARCHAR(16) NOT NULL DEFAULT 'fail' CHECK (status IN
     ('pass','fail','partial','n_a','unverified','error'))
   - Backfill: status = CASE WHEN passed THEN 'pass' WHEN
     reviewer_verdict IS NOT NULL THEN reviewer_verdict ELSE 'fail'
     END
   - Keep `passed` Boolean as derived for now (back-compat); plan
     drop in a future migration once all readers point at status.
   - database-reviewer agent auto-fires on alembic migration; address
     all CRIT+HIGH pre-push.

2. checkpoint_analyzer.py prompt:
   - Append guidance: "If the checkpoint name contains 'if applicable'
     or 'if relevant' and the transcript has no positive signal that
     the conditional fires, return status='n_a' with notes explaining
     why the condition does not apply."
   - Also add type-aware defaults: positive_obligation (default fail),
     negative_prohibition (default pass), conditional_obligation
     (default n_a when conditional doesn't fire).
   - Update _coerce_w4_fields to accept status='n_a'.

3. _step_score scoring math (pipeline.py):
   - score = passed / (total - n_a_count); n_a verdicts neither help
     nor hurt.
   - Same change in derive_compliance.segments_path worst-bucket
     derivation: n_a segments don't drag bucket down.

4. Frontend chip rendering (frontend-v3):
   - CheckpointCard.tsx: add n_a chip variant (grey/muted, distinct
     from emerald-pass / red-fail / amber-partial).
   - TrackerTable + CallDetail score chips: show "X/Y (Z n_a)" format.
   - code-reviewer agent auto-fires on frontend-v3/src/**/*.{ts,tsx};
     address all CRIT+HIGH pre-push.

5. Tests (3-5 regression cases):
   - tests/test_checkpoint_analyzer.py — `test_conditional_unfired_
     returns_n_a`, `test_n_a_excluded_from_score_denominator`,
     `test_negative_prohibition_silence_returns_pass`,
     `test_status_check_constraint_rejects_invalid`,
     `test_passed_boolean_derived_from_status`.

6. Validation:
   - Re-upload the Round 1 Zoe Larkins c call.mp3 (same audio as the
     2/26 baseline).
   - Sample 10 checkpoint verdicts on the call detail page. Score
     accuracy delta vs the 2/26 baseline. Owner needs concrete numbers.

PHASE 2 — D13 upload modal drops (NEW from yesterday's soak)
============================================================
Yesterday's 10-upload soak created 3 orphan "(pending audio upload)"
deal stubs without Call rows attached. The upload modal at /dashboard
lost track of 30 % of files under rapid sequential drops.

- Reproduce: upload 10 files in rapid sequence (4 rounds × 1+3+3+3),
  inspect /api/calls vs /api/deals for orphan stubs.
- Root cause: BatchUploadModal.tsx race condition on the per-file
  POST /api/calls/upload after the stub-deal POST resolves.
- Fix: gate stub-deal creation behind file-upload success, or wrap
  the file POST in a retry with exponential backoff for the
  413/503/network blip cases.

PHASE 3 — D14 residual loop_lag (NEW from yesterday's soak)
===========================================================
Off-loop file reads dropped lag from 13393ms → 1469ms but it's still
present. Profile sync paths in checkpoint_analyzer.py batch dispatch:

- json.loads / json.dumps on multi-KB LLM responses (run on event loop)
- fuzzy_match() — the regex / Levenshtein code path may be sync CPU
- Consider routing both through anyio.to_thread.run_sync.

PHASE 4 — D1/D2 BUSINESS_DETECT regression closeout
====================================================
Yesterday's first run returned only "Corner Cuts"; today's first run
returned full "Mrs. Zoe Larkins Trading As Corner Cuts" — needs 3-5
more samples to confirm the regression is closed. If still flaky,
tighten the prompt with a few-shot example.

DOCTRINE FOR THE WHOLE SESSION
==============================
- Read BRAIN before any tool call. Cite paths.
- LAW_OF_SKILLS v2.1: declare the trio in TodoWrite items 1-3 BEFORE
  the first state-mutating tool call. Invoke skills via Skill / Agent
  by name. Append ledger row per invocation.
- LAW_OF_ENTERPRISE_GRADE: 12-line checklist (schema, tests,
  observability, realtime, errors, idempotency, backwards-compat, UX,
  performance, security, audit, docs).
- database-reviewer auto-fires on alembic; code-reviewer on
  frontend-v3/src/**/*.{ts,tsx}; python-reviewer on backend/**/*.py;
  security-reviewer on auth-touched code.
- Push as kingusa1: `gh auth switch --user kingusa1` BEFORE every push.
- Auto-deploy: Railway picks up on push; Vercel needs the manual REST
  API curl in Known_Issues.md (GitHub App uninstalled on the repo).
- Run Session_Self_Audit before any "done" reply.

PROD ACCESS CHEAT SHEET
=======================
- Frontend: https://compliance-agent-mu.vercel.app
- Backend: https://compliance-agent-production-690e.up.railway.app
- Supabase token: pulled from localStorage key
  `sb-zcmdsblqbgatsrofptsq-auth-token`
- Railway: RAILWAY_TOKEN=4ac76051-95e8-4979-9a5d-98656396d4f5
- Railway CLI on Windows: C:\Users\kingu\AppData\Roaming\npm\railway.cmd
- Vercel: VERCEL_TOKEN at ~/.secrets/vercel.env

LATEST TIP COMMITS
==================
9e506e4 fix(db,pipeline,main): max-out config for Railway 24 vCPU + 24 GB Pro replica
27e16ec fix(db): bump pool_size 10→20, max_overflow 20→40 to absorb bulk-upload burst
cd6f157 fix(pipeline,transcribers): D9 widening + LAG fix — supplier-peel retryable re-raise + off-loop file reads
e745147 fix(tests): bump test_logged_step handler to DEBUG (CI GREEN)
4065e18 fix(db_retry,pipeline,sse): address python-reviewer findings on 211c299
211c299 fix(pipeline): retry pipeline step on QueryCanceled under bulk concurrency (D9)
```

---

## Why D10 is first

Phase 1 (D9 retry + LAG + max config) is **DONE** as of 2026-05-27 — zero `status=failed` under the new soak vs 70 % failure rate yesterday. The system is now reliability-stable.

The reviewer-blocking issue going forward is **AI verdict accuracy**: ~21 % of verdicts wrong (analyst report). Owner explicitly said *"i want it to be so accurate please make sure that the system data are accurate and everything is working 100% as intended"*. n_a vocabulary is the highest-leverage single fix — 16 phantom failures eliminated per call per pattern-1 analysis.

D13 (upload modal drops) and D14 (residual lag) are quality-of-life. D1/D2 is followup verification not fresh fix work.

## Files to skim before phase 1

1. `BRAIN/00_INDEX.md` — vault map
2. `BRAIN/05_State/Live_State.md` — today's tip + maxed-config state
3. `BRAIN/04_Sessions/2026_05_27_Session_d9_widening_lag_fix_max_config.md` — yesterday's full log
4. `BRAIN/04_Sessions/2026_05_26_Session_compliance_status_aggregation_fix.md` — section "AI verdict accuracy 5 patterns" (analyst report)
5. `BRAIN/05_State/Known_Issues.md` — D1-D14 register
6. `backend/app/checkpoint_analyzer.py` — grader prompt + status handling
7. `backend/app/models.py:311-349` — CallCheckpoint schema (current `passed` Boolean field)
8. `backend/app/pipeline.py:_step_score` + `app.compliance.derive_compliance.segments_path` — scoring math sites
9. `frontend-v3/src/components/CheckpointCard.tsx` — chip rendering
