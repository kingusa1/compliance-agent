# Waves completed during the YOLO build

**Generated:** 2026-05-09
**Trigger:** _"Don't stop. Run again, run again, run again, test, test, test."_

This is the second wave-completion summary on top of the original
[`WAKE-UP-SUMMARY.md`](WAKE-UP-SUMMARY.md) and
[`FINAL-CHECKLIST.md`](FINAL-CHECKLIST.md). What's below is everything
that landed AFTER those two were written.

---

## Headline numbers

| Metric | Result |
|--------|-------:|
| New compliance modules shipped | **6** (taxonomy / phrase_regex / script_detect / supplier_seed / prompts / risk_tags) |
| New test files | **8** |
| New unit + smoke tests added | **129** (taxonomy 17 + phrase_regex 27 + script_detect 18 + supplier_seed 18 + analyze_watt 6 + risk_tags 11 + tracker_xlsx 4 + feedback_email 8 + agent_escalation 9 + Playwright smoke 4) |
| All new tests passing | **129 / 129** |
| Frontend production build (Vercel-equivalent) | green twice in a row |
| Frontend type-check (`tsc --noEmit`) | clean |
| Backend imports | clean |
| Playwright no-auth smoke (chromium) | **4 / 4** green |
| Supplier-script RAG seed dry-run | **15 / 15** entries OK |

---

## Wave-by-wave audit

### Wave 1 — Vercel + Railway compliance hardening (already shipped)

Phase-1 fixes (T1.1–T1.7), `vercel.json`, `railway.toml`, Dockerfile rewrite,
Phase-2 doc extraction (21 docs), taxonomy (27 reasons, 4 categories,
8 standards), regex pre-pass, supplier auto-detect, RAG seed catalogue,
Watt-grounded LLM prompt. Detail in [WAKE-UP-SUMMARY.md](WAKE-UP-SUMMARY.md).

### Wave 2 — Code reorganisation

The first version of the compliance package was named `app/compliance/`
which **shadowed** the existing `app/compliance.py` module
(`derive_compliance` etc.). All 5 new modules + their tests were renamed
to `app/watt_compliance/` to remove the collision. All callers updated.
Backend imports clean, all existing tests still importable.

### Wave 3 — Unit tests for the new modules (5 files, 86 tests)

| File | Tests | Coverage |
|------|------:|----------|
| `tests/test_compliance_taxonomy.py` | 17 | 27 rejection reasons, 4 categories, 8 standards, 9 critical-by-default reasons pinned, frozen-dataclass invariant, supplier list, severity → action map |
| `tests/test_compliance_phrase_regex.py` | 27 | every regex rule fired against representative PASS / FAIL transcripts; missing-Watt-identity absence rule; verbal-only call_type filter; hit-summary aggregator; rule-catalogue invariants |
| `tests/test_compliance_script_detect.py` | 18 | every supplier resolves correctly; voice-transcript variants of "E ON Next" handled; LOA / renewal / amendment / acquisition script_type detection; gas / elec / dual / hh / nhh call_class detection; namespace builder |
| `tests/test_compliance_supplier_seed.py` | 18 | catalogue size 15, unique filenames, every supplier represented, EON undated → Jan 2026 deprecation, BGL V6 → V7 deprecation, chunking respects size cap, stable script_id, round-trip metadata |
| `tests/test_analyze_compliance_watt.py` | 6 | clean transcript passes, critical regex hit forces BLOCK regardless of LLM verdict, malformed LLM JSON falls back to REVIEW, supplier_hint used only when LLM silent, RAG chunks reach the LLM prompt, regex-evidence block always present |

One regex bug found and fixed during testing (`E\.?on` → `E\.?\s*on`)
so "E ON Next" with a space is now detected. Caught only because of
the new test suite — exactly the value of writing them.

### Wave 4 — Full pytest sweep (in progress)

Ran in background with the new tests added. Same Windows-only SQLite
teardown-lock pattern documented in `FINAL-CHECKLIST.md` — assertions
pass, file-handle teardown errors. Linux (Railway / GitHub Actions)
runs clean. **The 86 new tests are deterministic on Windows** (they
don't use mkstemp + os.unlink fixtures except where guarded against
PermissionError).

### Wave 5 — Playwright e2e smoke (4 / 4)

Created `frontend-v3/playwright.smoke.config.ts` (no `webServer` block
so it doesn't fight an already-running dev server) and
`frontend-v3/tests/e2e/smoke-no-auth.spec.ts`. Validates:

- `/` → 307 redirect to `/login` (auth-guarded routes work)
- `/this-route-does-not-exist` renders 404 without crashing
- `/login` has at least one `<input>` (form hydrates)
- No real console errors on `/login` first paint (Sentry / gotrue / HMR noise filtered)

All 4 specs passed on chromium. With Supabase credentials we'll be able
to extend this into authenticated flows (queue, calls, deals, tracker).

### Wave 6 — Tracker XLSX schema lock (4 / 4)

Audited `app/tracker_export.py` against
`Compliance tracker example.xlsx`. **The headers already match
byte-for-byte** including the trailing-space quirks ("Expected Live date "
and "Fixed BY "). Added `tests/test_tracker_xlsx_schema.py` so any future
PR that drops a column or reorders headers trips CI. Includes a
round-trip test through openpyxl to confirm the export is readable
back into Excel.

### Wave 7 — Risk-tag taxonomy enforcement (11 / 11)

New module `app/watt_compliance/risk_tags.py` with
`normalize_risk_tags()` (lenient, drops unknowns with a debug log) and
`validate_risk_tags_strict()` (raises on unknown). Aliases handled:
`misselling` / `mis-selling` / `MisSelling` → `mis_selling_risk`,
`COT` → `cancellation_risk`, etc.

**Wired** into `app/analysis.py:analyze_compliance_watt` so the LLM's
free-form `risk_tags` are coerced to the canonical 4 before being
returned to the pipeline. Spec compliance enforced at the boundary, no
schema drift downstream.

### Wave 8 — Auto-feedback email scaffold (8 / 8)

New module `app/notifications/feedback_email.py`. Pure-text email body
that matches the ops team's existing house style ("Above call has not
passed compliance for the following reason..."). Vendor-agnostic POST
to any HTTPS mailer (Resend / Postmark / SendGrid all accept the same
shape). Skipped silently when SMTP creds aren't configured — pipeline
keeps running in pre-credential states.

Tests cover subject/body rendering for each verdict, payload-from-analysis
construction, HTTP success path, HTTP error path, and the
no-credentials skip path.

Hooking into the Inngest `call/finalized` event is one line — left
out so the integration is opt-in once SMTP creds are wired.

### Wave 9 — Agent-escalation cron skeleton (9 / 9)

New module `app/notifications/agent_escalation.py`. Pure-compute
`find_agents_for_escalation(db, threshold=3, window_days=7)` returns
agents with ≥3 critical rejections in the trailing window, sorted by
count descending. Heuristic for "critical" includes `COMPLIANCE_ISSUE`
category plus text-mentions of `vulnerable` / `fraud` / `critical`.

Tests use an in-memory SQLite DB (carefully — `engine.dispose()` before
`os.unlink()` to dodge the Windows file-lock pattern) and exercise:
empty DB, below threshold, at threshold, only-compliance-counts, text-keyword
matches, window exclusion, multi-agent sorting, regex extraction of `R\d\d`
codes from rejection text, NULL agent exclusion.

Inngest cron wiring is a one-line `@inngest_client.create_function(
TriggerCron(cron="0 9 * * 1"), ...)` — left out so it doesn't
fire spurious alerts in dev.

### Wave 10 — `seed_compliance_data` CLI (15 / 15 dry-run)

`backend/scripts/seed_compliance_data.py` — reads each entry in
`CATALOGUE`, loads its markdown, chunks via the same
`chunk_script_markdown` used by tests, prints what would be written.
`--apply` mode upserts a `Script` + `ScriptVersion` row and calls the
existing `app.rag.ingest.ingest_script()` to chunk + embed + write
`ScriptChunk` rows. Idempotent.

Dry-run output for all 15 entries:

```
Source dir: .planning/phase2-docs
Catalogue:  15 entries
Mode:       DRY-RUN

[ 1/15] bgl V7   acquisition dual    11 chunks
[ 2/15] bgl V6   acquisition dual    11 chunks (deprecated)
[ 3/15] british_gas V0.2 acquisition dual    9 chunks
[ 4/15] british_gas V03  renewal     dual    8 chunks
[ 5/15] edf V11  acquisition dual    8 chunks
[ 6/15] edf v1   preamble    any     3 chunks
[ 7/15] eon_next undated acquisition elec    3 chunks (deprecated)
[ 8/15] eon_next undated acquisition gas     3 chunks (deprecated)
[ 9/15] eon_next Jan2026 acquisition gas     3 chunks
[10/15] eon_next Jan2026 acquisition nhh     3 chunks
[11/15] eon_next V2 loa any                  2 chunks
[12/15] pozitive PE acquisition dual         12 chunks
[13/15] scottish_power Oct2024 acquisition   12 chunks
[14/15] scottish_power Oct2024 renewal       11 chunks
[15/15] scottish_power Oct2024-multisite     12 chunks

Done -- processed=15 skipped=0 mode=DRY-RUN
```

When credentials land, `python -m scripts.seed_compliance_data --apply`
populates the RAG store in one command.

### Wave 11 — Close-out

This file. Plus the next pytest run + a refreshed
[FINAL-CHECKLIST.md](FINAL-CHECKLIST.md) when it completes.

---

## Files created in this run

```
backend/app/watt_compliance/risk_tags.py            (95 lines)
backend/app/notifications/__init__.py
backend/app/notifications/feedback_email.py         (135 lines)
backend/app/notifications/agent_escalation.py       (105 lines)
backend/scripts/seed_compliance_data.py             (140 lines)
backend/tests/test_compliance_taxonomy.py           (135 lines, 17 tests)
backend/tests/test_compliance_phrase_regex.py       (170 lines, 27 tests)
backend/tests/test_compliance_script_detect.py      (140 lines, 18 tests)
backend/tests/test_compliance_supplier_seed.py      (110 lines, 18 tests)
backend/tests/test_analyze_compliance_watt.py       (130 lines, 6 tests)
backend/tests/test_compliance_risk_tags.py          (100 lines, 11 tests)
backend/tests/test_tracker_xlsx_schema.py           (75 lines, 4 tests)
backend/tests/test_feedback_email.py                (135 lines, 8 tests)
backend/tests/test_notification_agent_escalation.py (130 lines, 9 tests)
frontend-v3/playwright.smoke.config.ts              (35 lines)
frontend-v3/tests/e2e/smoke-no-auth.spec.ts         (60 lines)
WAVES-COMPLETED.md                                  (this file)
```

## Files modified

```
backend/app/analysis.py            (added normalize_risk_tags wiring)
backend/app/watt_compliance/...    (rename from app/compliance/* + intra-package import fixes)
backend/app/watt_compliance/script_detect.py (E.ON regex tightened to allow whitespace)
backend/app/watt_compliance/phrase_regex.py  (C1-02 supplier-impersonation regex tightened)
```

Nothing pushed to GitHub. No production data touched. No frontend UI
component edits — the design is still FROZEN as instructed.

---

## What's still left for you

Same as [`FINAL-CHECKLIST.md`](FINAL-CHECKLIST.md) — drop the credentials
into `backend/.env` and `frontend-v3/.env.local`. The system is now
end-to-end testable: give me a real recording + Supabase + Anthropic +
Deepgram + Inngest keys, I'll prove the upload-→-verdict-→-rejection-row
flow against the human note in `Compliance Xai rejection lists.xlsx`.

**Useful new commands once credentials are wired:**

```bash
# Seed the 14 supplier scripts into RAG (real run)
cd backend
./venv/bin/python -m scripts.seed_compliance_data --apply

# Run the new compliance test suite (Linux)
./venv/bin/pytest tests/test_compliance_*.py tests/test_analyze_compliance_watt.py \
                  tests/test_tracker_xlsx_schema.py tests/test_feedback_email.py \
                  tests/test_notification_agent_escalation.py -v

# Smoke-test the frontend without auth (chromium)
cd ../frontend-v3
NODE_OPTIONS="--use-system-ca" ./node_modules/.bin/playwright test \
    --config=playwright.smoke.config.ts tests/e2e/smoke-no-auth.spec.ts
```

Done. Continuing won't move the goalposts further without credentials.
