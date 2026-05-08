# Test Cleanup — Green CI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Apply `two-stage-review-loop` between tasks.

**Goal:** Bring `pytest` + `vitest` to **0 failures** on the same shape CI runs (no `.env` for backend; stub Supabase for frontend), so PRs #1-#4 can merge with green CI.

**Architecture:** Three sub-blocks land in one wave.
(a) **Cloud-schema sync** — apply pending Alembic migration `6c863e1ce3b1_failed_jobs` to cloud Supabase. Fixes 8 tests immediately.
(b) **Test cleanup, file-by-file** — 12 backend test files have drift since last green run. Each gets one commit fixing tests OR fixing the underlying logic the test guards. Prioritise files with most failures.
(c) **Frontend CheckpointCard drift** — component rewrote, 4 tests use stale text matchers. Rewrite tests against current component.

**Tech Stack:** pytest 8.3, alembic 1.14, postgres (cloud Supabase pooler), vitest 2.x, React Testing Library.

**Spec source:** Local pytest run 2026-05-07 with full `backend/.env` produced 52 failures, 482 passes, 6 skips. Full failure list captured at `/tmp/fails.txt`. Frontend vitest 4 failures, 71 passes.

**Why 159 → 52:** 107 of the original 159 failures were caused by missing `.env` in CI — Pydantic Settings refused to import without `deepgram_api_key`. The `fix(ci): make deepgram_api_key optional` commit (already on `feat/wave5-deploy`) collapses those 107. Remaining 52 are real test debt categorized below.

**Failure categories:**

| Bucket | Files | Count | Root cause |
|---|---|---|---|
| Cloud schema sync | test_failed_jobs (4), test_observability_routes_audit (2), test_replay (2) | 8 | Alembic head `6c863e1ce3b1` unapplied on cloud Supabase |
| Pipeline-shape drift | test_workflows (3) | 3 | Wave 2 `record_pipeline_step` instrumentation changed step.run boundary structure |
| Word-match drift | test_word_match (5) | 5 | Unicode quote handling + paraphrase token-overlap logic regressed |
| Workflow integration | test_integration (6), test_graceful_degradation (6), test_pipeline (2) | 14 | Full-pipeline tests that need provider mocks; flaky against real APIs |
| Auth/HITL drift | test_auth, test_release, test_history, test_verdict (3), test_compliance_override (3), test_prompt_versioning, test_routes (3), test_deals_stub | 14 | Various — auth signature shifts, response shape drift |
| Rejection / portal | test_rejections (3), test_portal_batches_and_dead_reasons | 4 | Admin-only enforcement drifted |
| AI category | test_ai_category_suggestion (3), test_vulnerability | 4 | Provider mock missing for new code paths |
| Frontend | tests/unit/CheckpointCard.test.tsx | 4 | Component rewrote — stale text matchers |
| **Total** | | **56** | (52 backend + 4 frontend) |

**Branch:** Cut from `feat/wave5-deploy` so all CI fixes stack on the existing PR chain. Push directly to `feat/wave5-deploy` so PR #4 absorbs the fixes.

**Skip-tracking:** Tests that turn out to be **real bugs** (not drift) get a dedicated commit fixing the underlying code, NEVER `@pytest.mark.skip`. Skip markers are forbidden — they hide regressions. If a test cannot be fixed within this plan, leave it failing and document in `claude-progress.txt` as Phase-2 work.

---

## Task 1: Apply pending Alembic migration to cloud Supabase

**Files:**
- (Read-only check): `backend/alembic/versions/6c863e1ce3b1_failed_jobs.py`
- (Action): Run `alembic upgrade head` against cloud Supabase pooler

- [ ] **Step 1: Confirm current state**

```bash
cd /Users/gomaa/Documents/Compliance/backend && ./venv/bin/alembic current
```
Expected: shows revision `d4e5a6b7c8d9` (or similar — the parent of `6c863e1ce3b1`).

```bash
./venv/bin/alembic heads
```
Expected: shows `6c863e1ce3b1 (head)`.

- [ ] **Step 2: Inspect the pending migration**

```bash
cat backend/alembic/versions/6c863e1ce3b1_failed_jobs.py
```
Verify it creates `failed_jobs` table with columns `id, call_id, last_step, attempts, last_error, exhausted_at, created_at` and proper indexes (per Wave 1 spec). Read the upgrade body — confirm no destructive ops on existing tables.

- [ ] **Step 3: Run migration on cloud DB**

```bash
cd backend && ./venv/bin/alembic upgrade head
```
Expected output:
```
INFO  [alembic.runtime.migration] Running upgrade d4e5a6b7c8d9 -> 6c863e1ce3b1, failed_jobs table
```

If the migration uses a connection that references `MIGRATION_DATABASE_URL` (port 5432, session pooler), the env loaded via `backend/.env` already provides it.

- [ ] **Step 4: Verify schema applied**

```bash
./venv/bin/python -c "
from app.database import engine
from sqlalchemy import text
with engine.connect() as c:
    cols = c.execute(text(
        \"SELECT column_name, data_type FROM information_schema.columns \"
        \"WHERE table_name = 'failed_jobs' ORDER BY ordinal_position\"
    )).fetchall()
    print(cols)
"
```
Expected: prints the 7 columns. Empty list → migration didn't run.

- [ ] **Step 5: Re-run the previously-failing tests**

```bash
./venv/bin/python -m pytest tests/test_failed_jobs.py tests/test_observability_routes_audit.py -v
```
Expected: 6 passed (4 in test_failed_jobs + 2 in test_observability_routes_audit).

- [ ] **Step 6: Commit a runbook note (no code change)**

The migration touched cloud DB, not the repo. Document in `claude-progress.txt`:

```bash
cat >> /Users/gomaa/Documents/Compliance/claude-progress.txt <<'EOF'

[2026-05-07] CLOUD DB: alembic upgrade head applied 6c863e1ce3b1_failed_jobs
to cloud Supabase. Schema now matches repo head. Verified test_failed_jobs +
test_observability_routes_audit go from 6 fail → 6 pass.
EOF
git add claude-progress.txt
git commit -m "ops(db): apply 6c863e1ce3b1_failed_jobs migration to cloud Supabase"
```

---

## Task 2: Fix `test_replay.py` failures

**Files:**
- Modify: `backend/tests/conftest.py` (fixture cleanup hardening)
- (Possibly) Modify: `backend/app/replay.py` (only if real bug surfaces)

- [ ] **Step 1: Capture exact failure**

```bash
cd backend && ./venv/bin/python -m pytest tests/test_replay.py -v --tb=short 2>&1 | tail -40
```
Capture the assertion message + line numbers.

- [ ] **Step 2: Diagnose**

The failures appeared as `ERROR` (collection-time, not assertion). Likely root cause: conftest fixture `db_session_with_call_with_transcript` creates rows in a DB that may have stale data from prior runs — UNIQUE constraint or audit_log hash chain mismatch.

Verify by:
```bash
./venv/bin/python -m pytest tests/test_replay.py::test_reanalyze_returns_404_for_unknown_call_id -v
```
This test doesn't use the fixture. If it passes alone, the other two are fixture-related.

- [ ] **Step 3: Harden the fixtures**

In `backend/tests/conftest.py`, find the two `db_session_with_call_*` fixtures (added in W3-T6). The `try / yield / finally` block does `db.rollback()` then `db.close()` but commits the seed rows before yielding. Those rows persist across runs.

Replace the teardown:

```python
    finally:
        # Best-effort cleanup — delete seeded rows by id so reruns don't pile up.
        try:
            db.query(Call).filter(Call.id == call.id).delete()
            db.query(Script).filter(Script.id == script.id).delete()
            db.commit()
        except Exception:
            db.rollback()
        db.close()
```

(Apply identically to both `db_session_with_call_with_transcript` and `db_session_with_call_no_transcript`.)

- [ ] **Step 4: Re-run**

```bash
./venv/bin/python -m pytest tests/test_replay.py -v
```
Expected: 3 passed (the 2 previously failing + the 1 already passing).

- [ ] **Step 5: Commit**

```bash
git add backend/tests/conftest.py
git commit -m "fix(tests): clean up Call+Script seed rows in replay fixtures so reruns don't collide"
```

---

## Task 3: Fix `test_workflows.py` — pipeline-shape drift

**Files:**
- Modify: `backend/tests/test_workflows.py`

The 3 failing tests assert specific shape of the pipeline (e.g. "six step.run boundaries in order"). Wave 2 added `record_pipeline_step` calls outside the step.run wrappers, but the actual step boundaries should still be 6 (download_audio, transcribe, detect_metadata, analyze_checkpoints, score, finalize). Tests likely use a brittle string-matching approach that broke when timing wrappers were added.

- [ ] **Step 1: Capture exact failure**

```bash
./venv/bin/python -m pytest tests/test_workflows.py -v --tb=short 2>&1 | tail -30
```

- [ ] **Step 2: Read the failing tests**

```bash
grep -n "test_workflow_has_six_step_run_boundaries\|test_logged_step_emits_workflow_step_logs_on_success\|test_logged_step_emits_err_log_and_reraises_on_failure" backend/tests/test_workflows.py
```

Read each. Likely failures:
- `test_workflow_has_six_step_run_boundaries_in_order` — counts `ctx.step.run("..."` in the source by regex; passing if exactly 6 found in correct order. Wave 2 didn't change count, so this should still pass — investigate why it fails. Possible: code text changed enough that the regex no longer matches.
- `test_logged_step_emits_workflow_step_logs_on_success` — patches `app_log` and asserts a specific log line. Wave 2 changed log format to JSON; assertion likely matches the old plain-text format.
- `test_logged_step_emits_err_log_and_reraises_on_failure` — same shape; new JSON formatter changes the substring assertion.

- [ ] **Step 3: Fix the assertions**

Open `backend/tests/test_workflows.py`. For each failing test:

- For the regex/count test: re-capture the actual `ctx.step.run` calls via `grep -c "await ctx.step.run" backend/app/workflows/process_call.py`. If 6 still, the test's regex needs adjusting (probably `_logged_step` wrapper changed argument shape). Update regex to match current shape: `await ctx.step.run\(\s*"<name>",\s*_logged_step\(...\)`.
- For the log-assertion tests: the JSON formatter renders dicts. Replace `assert "WORKFLOW_STEP_OK ..." in caplog.text` with `assert any('"step": "<name>"' in r.message or '<name>' in r.getMessage() for r in caplog.records)`. Use the structured fields, not stringified output.

If the tests were written before Wave 2 instrumentation, update them to assert what we actually emit now (per `app/logger.py` JSON shape).

Insert exact fixed versions inline (read the actual current test bodies first; the engineer copies the new assertions into place).

- [ ] **Step 4: Re-run**

```bash
./venv/bin/python -m pytest tests/test_workflows.py -v
```
Expected: 3 previously-failing pass.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_workflows.py
git commit -m "fix(tests): update test_workflows assertions to match Wave 2 JSON logger + step.run wrappers"
```

---

## Task 4: Fix `test_word_match.py` — 5 failures

**Files:**
- Modify: `backend/app/word_match.py` OR `backend/tests/test_word_match.py` depending on which side drifted.

These tests guard the evidence-extraction logic (matching transcript phrases to checkpoint scripts). Failures:
1. `test_exact_match_returns_first_and_last_word_timestamps` — regression in timestamp-bracketing
2. `test_paraphrase_still_matches_when_token_overlap_above_threshold` — token-overlap threshold drift
3. `test_wrapped_evidence_with_speaker_labels_and_quotes` — speaker-label stripping broke
4. `test_unicode_curly_quotes_are_normalized` — `'` `"` not normalized to ASCII `'` `"`
5. `test_single_word_evidence_still_matches` — single-word match dropped

- [ ] **Step 1: Capture failures with full context**

```bash
./venv/bin/python -m pytest tests/test_word_match.py -v --tb=long 2>&1 | tail -80
```

- [ ] **Step 2: Decide drift vs bug**

Pick one failing test, run it solo, read the assertion + actual values. If the actual output looks "wrong" to a human reading a transcript, it's a real bug — fix `word_match.py`. If the test assertion is overly strict (e.g. expects exact string but actual is semantically equivalent), update the test.

For unicode normalization specifically: real bug. Compliance verdicts must not depend on quote style. Fix in `word_match.py` by adding `unicodedata.normalize` + a regex map for curly-to-straight quotes BEFORE the substring search.

- [ ] **Step 3: Fix `word_match.py`**

In `backend/app/word_match.py`, near the top of the matching function (find via `grep -n "def find_evidence\|def match_evidence" word_match.py`), add a normaliser:

```python
import unicodedata

_CURLY_QUOTES_MAP = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", " ": " ",
})


def _normalize(text: str) -> str:
    """NFKC + curly→straight quotes + collapse whitespace."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_CURLY_QUOTES_MAP)
    text = " ".join(text.split())
    return text
```

Apply `_normalize(...)` to BOTH the script text and the transcript text before any substring or token-overlap comparison.

- [ ] **Step 4: Run the unicode test**

```bash
./venv/bin/python -m pytest tests/test_word_match.py::test_unicode_curly_quotes_are_normalized -v
```
Expected: PASS.

- [ ] **Step 5: For the other 4 failures**

Read each assertion. If the test expects behavior that the current code doesn't produce, decide:
- Test is wrong (overly specific) → update test.
- Code is wrong (regressed behavior) → fix code.

Likely fixes inline based on what actual output shows. Do NOT skip; fix or commit the test rewrite. Each test gets one commit.

Sample fix for `test_single_word_evidence_still_matches` if the issue is single-token edge: in `word_match.py`'s match function, if input is exactly 1 token, skip the overlap-threshold gate (always include the match). Add a one-line guard:

```python
if len(script_tokens) == 1:
    threshold = 0.0  # single-word matches always count
```

- [ ] **Step 6: Run the full word_match suite**

```bash
./venv/bin/python -m pytest tests/test_word_match.py -v
```
Expected: 5 + N passing (where N = previously-passing).

- [ ] **Step 7: Commit**

```bash
git add backend/app/word_match.py backend/tests/test_word_match.py
git commit -m "fix(word_match): NFKC normalize + curly-quote folding + single-word match guard"
```

---

## Task 5: Fix `test_graceful_degradation.py` — 6 failures

**Files:**
- Modify: `backend/tests/test_graceful_degradation.py` OR underlying logic

These tests guard the rule that calls with mostly-error checkpoints get marked `manual_review` and not auto-pass. Failures span thresholds (single-error, half-errors, all-errors). Likely root cause: error-counting logic in `app/compliance.py` or `app/deal_verdict.py` shifted with Wave 2 metric instrumentation OR an enum value rename.

- [ ] **Step 1: Capture failures**

```bash
./venv/bin/python -m pytest tests/test_graceful_degradation.py -v --tb=short 2>&1 | tail -40
```

- [ ] **Step 2: Diagnose by running solo + reading assertion**

Pick `test_single_error_call_completes_with_adjusted_score`. Read it. The test calls a function (likely `derive_compliance` or `score`) with synthetic checkpoints, expects a specific score range or status string. The actual output mismatch tells you which side drifted.

- [ ] **Step 3: Fix the underlying logic**

If the test expects:
- `status="completed"` but code emits `"complete"` → enum rename. Standardize on one form across the file by grep + Edit-replace.
- `score>0` but code emits `0` → adjustment formula regressed. Find the score-adjustment helper and verify it accounts for error count.

Apply minimal fix in `app/compliance.py` or `app/deal_verdict.py` matching the test's contract. The tests are correct — the spec demands manual_review on majority errors.

- [ ] **Step 4: Run all 6**

```bash
./venv/bin/python -m pytest tests/test_graceful_degradation.py -v
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/compliance.py backend/app/deal_verdict.py backend/tests/test_graceful_degradation.py
git commit -m "fix(compliance): restore graceful-degradation thresholds (single/half/majority errors → manual_review)"
```

---

## Task 6: Fix `test_integration.py` — 6 failures

**Files:**
- Modify: `backend/tests/test_integration.py` (most likely — fixtures probably stale)

Full-pipeline integration tests. Failures include `test_integration_compliant_call_v2`, `test_integration_non_compliant_call_v2`, `test_integration_unknown_supplier_fallback_v1`, `test_integration_llm_timeout_graceful_degradation`, `test_integration_partial_checkpoint_v2`, `test_integration_explicit_script_id_skips_detection`. These exercise the full pipeline mock-providers + audit_log + verdict path.

- [ ] **Step 1: Capture failures**

```bash
./venv/bin/python -m pytest tests/test_integration.py -v --tb=short 2>&1 | tail -60
```

- [ ] **Step 2: Look for common root cause**

Read each failure. Likely common pattern: pipeline added a step (Wave 2 detect_metadata or Wave 3 reanalyze hook) that integration tests don't account for. Or audit_log writes block a test that assumes empty audit table.

- [ ] **Step 3: Fix the test fixtures**

Most likely fix: tests construct a synthetic Call + Script + run the pipeline; assertion checks the final verdict. Pipeline now also writes `audit_log` rows + `failed_jobs` rows. Update tests' setup/teardown to clean these tables OR skip auditing in tests via a fixture-injected `record_audit` mock.

- [ ] **Step 4: Run**

```bash
./venv/bin/python -m pytest tests/test_integration.py -v
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_integration.py
git commit -m "fix(tests): integration suite — clean audit/failed_jobs side-effects + handle Wave 2 detect_metadata step"
```

---

## Task 7: Fix `test_pipeline.py` — 2 failures

**Files:**
- Modify: `backend/tests/test_pipeline.py`

`test_process_call_v1_with_checkpoints` and `test_process_call_failure_marks_failed` — both Wave 2 process_call drift. Per claude-progress.txt these were noted as pre-existing failures (mocks of `app.pipeline.transcribe_audio` that no longer exists or moved).

- [ ] **Step 1: Capture failures**

```bash
./venv/bin/python -m pytest tests/test_pipeline.py -v --tb=short 2>&1 | tail -30
```

- [ ] **Step 2: Find the moved/missing symbol**

```bash
grep -rn "def transcribe_audio\|def _step_transcribe" backend/app/pipeline.py backend/app/transcription.py
```

If `transcribe_audio` was renamed to `_step_transcribe` (Wave 1 refactor) the test mock target needs updating.

- [ ] **Step 3: Update test mock targets**

In `backend/tests/test_pipeline.py`, change `patch("app.pipeline.transcribe_audio", ...)` to the actual current symbol. Likely `patch("app.pipeline._step_transcribe", ...)`. Verify by reading the test setup; replace exactly.

- [ ] **Step 4: Run**

```bash
./venv/bin/python -m pytest tests/test_pipeline.py -v
```
Expected: 2 previously-failing now pass.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_pipeline.py
git commit -m "fix(tests): pipeline mock targets follow _step_transcribe rename"
```

---

## Task 8: Fix `test_routes.py` (3) + `test_auth.py` (1) — auth/route drift

**Files:**
- Modify: `backend/tests/test_routes.py`
- Modify: `backend/tests/test_auth.py`

- [ ] **Step 1: Capture**

```bash
./venv/bin/python -m pytest tests/test_routes.py tests/test_auth.py -v --tb=short 2>&1 | tail -40
```

- [ ] **Step 2: Identify each failure individually**

`test_current_user_returns_profile_fields`: likely impacted by Wave 4 `DEV_ALL_ADMIN` flag — when env has `DEV_ALL_ADMIN=true`, role is overridden to `admin` regardless of stored role. Test expected reviewer role.

Fix: in the test, monkeypatch `settings.dev_all_admin = False` to force the real role path, OR update the assertion to reflect the new flag-aware contract.

`test_list_calls_includes_checkpoints`, `test_upload_invalid_type`, `test_retry_call_wrong_status`: likely response-shape drift. Read each, apply targeted fix.

- [ ] **Step 3: Apply fixes inline**

For test_auth specifically:

```python
def test_current_user_returns_profile_fields(monkeypatch, ...):
    monkeypatch.setattr("app.config.settings.dev_all_admin", False)  # force real role
    ...
```

For the others: read assertions, compare to current API output, update to match current shape (e.g. new fields added to response).

- [ ] **Step 4: Run**

```bash
./venv/bin/python -m pytest tests/test_routes.py tests/test_auth.py -v
```
Expected: 4 previously-failing pass.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_routes.py backend/tests/test_auth.py
git commit -m "fix(tests): route/auth assertions match current API shape + dev_all_admin override"
```

---

## Task 9: Fix HITL/verdict/release/history/compliance_override tests — 11 failures

**Files:**
- Modify: `backend/tests/test_release.py`
- Modify: `backend/tests/test_history.py`
- Modify: `backend/tests/test_verdict.py`
- Modify: `backend/tests/test_compliance_override.py`
- Modify: `backend/tests/test_prompt_versioning.py`

Same drift pattern: test expectations vs current API. Each test gets read, compared to current behaviour, updated.

- [ ] **Step 1: Run + capture**

```bash
./venv/bin/python -m pytest tests/test_release.py tests/test_history.py tests/test_verdict.py tests/test_compliance_override.py tests/test_prompt_versioning.py -v --tb=short 2>&1 | tail -60
```

- [ ] **Step 2: Group by root cause**

Likely groupings:
- All HITL tests assume `dev_all_admin=False` (lock-acquisition by reviewer A then B → release/claim semantics). When override on, role-checks short-circuit. Add fixture-level monkeypatch.
- Verdict-history tests may rely on `current_prompt_version` field which Wave 1 added. Re-read the schema.
- Compliance-override tests assume `comment` field required when overriding to non-current status; might pass currently as enum names changed.

- [ ] **Step 3: Add a shared `_no_dev_admin` fixture in conftest**

In `backend/tests/conftest.py`:

```python
@pytest.fixture
def no_dev_admin(monkeypatch):
    """Force settings.dev_all_admin=False for tests that exercise role gates."""
    monkeypatch.setattr("app.config.settings.dev_all_admin", False)
    yield
```

Add `no_dev_admin` to test signatures of HITL/verdict tests that depend on real reviewer roles.

- [ ] **Step 4: Apply targeted fixes per file**

For each remaining assertion mismatch, update to match current behaviour. NEVER skip; if a real bug surfaces, fix it in code.

- [ ] **Step 5: Run**

```bash
./venv/bin/python -m pytest tests/test_release.py tests/test_history.py tests/test_verdict.py tests/test_compliance_override.py tests/test_prompt_versioning.py -v
```
Expected: 11 previously-failing pass.

- [ ] **Step 6: Commit**

```bash
git add backend/tests/conftest.py backend/tests/test_release.py backend/tests/test_history.py backend/tests/test_verdict.py backend/tests/test_compliance_override.py backend/tests/test_prompt_versioning.py
git commit -m "fix(tests): HITL/verdict suites use no_dev_admin fixture to force real role gates"
```

---

## Task 10: Fix `test_rejections.py` (3) + `test_portal_batches_and_dead_reasons.py` (1) — admin-only enforcement

**Files:**
- Modify: relevant test files

Same root cause as Task 9: `DEV_ALL_ADMIN=true` makes admin-gate tests fail because every user is admin → "non-admin cannot create" reverses into "admin can create" which is what the route allows.

- [ ] **Step 1: Apply `no_dev_admin` fixture**

Add `no_dev_admin` fixture (defined in Task 9) to each affected test:

```python
def test_non_admin_cannot_create(no_dev_admin, ...):
    ...
```

- [ ] **Step 2: Run**

```bash
./venv/bin/python -m pytest tests/test_rejections.py tests/test_portal_batches_and_dead_reasons.py -v
```
Expected: 4 previously-failing pass.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_rejections.py backend/tests/test_portal_batches_and_dead_reasons.py
git commit -m "fix(tests): rejections+portal admin-gate tests use no_dev_admin"
```

---

## Task 11: Fix `test_ai_category_suggestion.py` (3) + `test_vulnerability.py` (1) — provider mocks

**Files:**
- Modify: `backend/tests/test_ai_category_suggestion.py`
- Modify: `backend/tests/test_vulnerability.py`

These exercise AI-backed paths. Likely failures: provider mock not set up, or vulnerability flag-gate (Wave 0 `vulnerable_detection_enabled`) toggled differently in test env.

- [ ] **Step 1: Run + capture**

```bash
./venv/bin/python -m pytest tests/test_ai_category_suggestion.py tests/test_vulnerability.py -v --tb=short 2>&1 | tail -30
```

- [ ] **Step 2: Read each failing test**

For `test_detect_respects_feature_flag_off`: monkeypatch `settings.vulnerable_detection_enabled=False` and assert no flag emitted. Should be a 1-line `monkeypatch.setattr` fix if the test isn't already doing it.

For ai_category_suggestion tests: probably need provider response mocks. Add `unittest.mock.patch` around the agent call.

- [ ] **Step 3: Apply fixes inline per test**

- [ ] **Step 4: Run**

```bash
./venv/bin/python -m pytest tests/test_ai_category_suggestion.py tests/test_vulnerability.py -v
```
Expected: 4 previously-failing pass.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_ai_category_suggestion.py backend/tests/test_vulnerability.py
git commit -m "fix(tests): ai_category + vulnerability — mock providers + force feature flags off"
```

---

## Task 12: Fix `test_deals_stub.py` — 1 failure

**Files:**
- Modify: `backend/tests/test_deals_stub.py`

`test_post_deals_stub_returns_uuid`: Wave 1 added `record_audit("deal_create_stub")` to the route. Test may not handle the audit-side-effect or DB row creation correctly.

- [ ] **Step 1: Run + read**

```bash
./venv/bin/python -m pytest tests/test_deals_stub.py -v --tb=short 2>&1 | tail -20
```

- [ ] **Step 2: Apply fix**

Likely needs cleanup of the seeded deal stub row in teardown, OR audit-row count assertion update.

- [ ] **Step 3: Run**

```bash
./venv/bin/python -m pytest tests/test_deals_stub.py -v
```
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_deals_stub.py
git commit -m "fix(tests): deals_stub — clean seeded row in teardown to avoid UUID collision on rerun"
```

---

## Task 13: Fix frontend `tests/unit/CheckpointCard.test.tsx` — 4 failures

**Files:**
- Modify: `frontend-v3/tests/unit/CheckpointCard.test.tsx`

Component drifted. Tests use stale text matchers like `/Script rule/i` and `/Play from/i`. `Play from` now appears multiple times in DOM (probably one per checkpoint variant) so `getByLabelText` returns multiple matches.

- [ ] **Step 1: Read current component**

```bash
cat /Users/gomaa/Documents/Compliance/frontend-v3/src/app/\(reviewer\)/calls/\[id\]/CheckpointCard.tsx | head -80
```

Identify what text/aria-labels the component actually renders today.

- [ ] **Step 2: Read failing test**

```bash
cat /Users/gomaa/Documents/Compliance/frontend-v3/tests/unit/CheckpointCard.test.tsx
```

For each failing test, replace stale matchers:
- `getByText(/Script rule/i)` → `getByRole('heading', { name: /script rule/i })` if the heading still exists, OR drop the assertion if the section was renamed.
- `getByLabelText(/Play from/i)` → `getAllByLabelText(/Play from/i)[0]` when multiple instances are expected, OR scope to a region first.
- `getByText(/PARTIAL/)` → check current badge component; might be `/partial/i` or `data-status="partial"`.

- [ ] **Step 3: Update tests**

Apply specific replacements based on what the component actually renders. No skip markers. If the assertion no longer makes sense (e.g. tests a feature that was removed), delete the test outright with a one-line comment in commit body explaining why.

- [ ] **Step 4: Run**

```bash
cd frontend-v3 && npm run test:unit -- --run tests/unit/CheckpointCard.test.tsx
```
Expected: 4 previously-failing now pass (or fewer tests if some were deleted as obsolete; document deletions in commit body).

- [ ] **Step 5: Run full vitest suite to ensure no regression**

```bash
npm run test:unit -- --run
```
Expected: all green.

- [ ] **Step 6: Commit**

```bash
cd /Users/gomaa/Documents/Compliance && git add frontend-v3/tests/unit/CheckpointCard.test.tsx
git commit -m "fix(tests): CheckpointCard.test.tsx matchers track current component output"
```

---

## Task 14: Final full-suite verification

**Files:** none

- [ ] **Step 1: Backend full pytest, no `.env`**

```bash
cd /Users/gomaa/Documents/Compliance/backend && mv .env .env.bak
./venv/bin/python -m pytest --tb=line -q 2>&1 | tail -5
mv .env.bak .env
```
Expected: `0 failed, ~540 passed` (allow for warnings + skipped).

- [ ] **Step 2: Backend full pytest, with `.env`**

```bash
./venv/bin/python -m pytest --tb=line -q 2>&1 | tail -5
```
Expected: same `0 failed`.

- [ ] **Step 3: Frontend full vitest**

```bash
cd /Users/gomaa/Documents/Compliance/frontend-v3 && npm run test:unit -- --run 2>&1 | tail -5
```
Expected: 0 failed.

- [ ] **Step 4: Push + verify CI on PR #4**

```bash
cd /Users/gomaa/Documents/Compliance && git push origin feat/wave5-deploy
```

Then watch:
```bash
GH_TOKEN=$(gh auth token) gh pr checks 4 --watch
```
Expected: pytest, vitest, coverage, gate all `pass`.

- [ ] **Step 5: Document in claude-progress.txt**

Append:

```
[2026-05-07] CI GREEN: backend pytest 0 fail (was 159), frontend vitest 0 fail (was 4).
PR #4 (and stacked PRs #1-3 after rebase) now mergeable.
Cloud Supabase migrated to alembic head 6c863e1ce3b1.
```

```bash
git add claude-progress.txt
git commit -m "docs(progress): green CI — 159 → 0 fail across backend + frontend test suites"
git push
```

---

## Acceptance gate

- [ ] Backend pytest: 0 failed (verified both with and without `.env`)
- [ ] Frontend vitest: 0 failed
- [ ] CI on PR #4 reports green for `pytest`, `vitest`, `coverage`, `gate`
- [ ] No `@pytest.mark.skip`-style skip markers introduced (zero tolerance)
- [ ] No `xfail` markers introduced (same rule)
- [ ] Each fix-commit references the test file it touches in the message
- [ ] `claude-progress.txt` updated with CI-green entry

After CI green, PRs #1-#4 mergeable in dependency order: #1 → #2 → #3 → #4.

## Notes on cost

Real failure analysis time: 5-min pytest run twice (with/without `.env`) + per-file investigation. Total wall time estimate: 4-6 hours of focused work. Compared to D-option original estimate of "1-2 days" this plan front-loads the cheapest wins (cloud migration → 8 free passes; provider stubs → many free passes).

## Out of scope

- New tests for Wave 2-5 features that don't have them yet (covered by their own waves)
- Coverage ratchet from 50% → 75% (separate Phase-2 task once green)
- E2E playwright tests (label-gated, not blocking CI)
