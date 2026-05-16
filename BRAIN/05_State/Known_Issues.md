---
created: 2026-05-10
updated: 2026-05-16
tags: [state, issues, gotchas, stale-tests]
---

# Known issues / gotchas

## 🚨 Stale-test pattern — CI red after CHECK / bucket-gate / auth-dep changes (2026-05-16)

**Symptom**: GitHub Actions `coverage` workflow turns red within 1-2 commits of any production change that:
- alters a CHECK-constrained enum value (e.g. `ck_flags_risk_tag`, `ck_call_segments_stage`),
- shifts a severity-bucket threshold in `app/checkpoint_analyzer.py`,
- adds `Depends(current_reviewer)` / `Depends(_require_admin)` to a route,
- or moves a column write (e.g. `Rejection.outcome_narrative` → `Rejection.fix_narrative`).

**Diagnosis**: tests asserting the OLD behaviour are still on disk. The production change shipped solo; the test wasn't updated.

**Recurring instances (each red 5-6 commits before being caught)**:

| When | Production change | Stale test |
|---|---|---|
| 2026-05-15 | AI narrative → `Rejection.fix_narrative` | `test_ai_rejection_reason::test_ai_rejection_reason_propagates_to_rejection_row` |
| 2026-05-15 | `Depends(current_reviewer)` on `/api/calls/{id}/retry` | `test_routes.py::test_retry_call_*` (×4) returning 401 |
| 2026-05-15 (`a83e441`) | Medium-only pass-rate gate: <50% → `review` not `coaching` | `test_checkpoint_analyzer::test_all_checkpoints_mixed_results` |
| 2026-05-16 (`e1c8d3b`) | `vulnerability.risk_tag` → `None` (ck_flags_risk_tag CHECK) | `test_vulnerability::test_detect_emits_medium_when_only_stage1_fires`, `test_detect_emits_high_when_both_stages_agree` |

**Fix recipe** (codified in [[../06_Operations/Skill_Routing#Anti-patterns]]):

Before pushing any commit that mutates one of the patterns above, grep:

```bash
grep -rn "risk_tag.*Vulnerable\|bucket.*coaching\|outcome_narrative\|Depends(current_reviewer)" backend/tests/
```

If a hit references the OLD behaviour, update the test in the same commit. The fix for the 2026-05-16 instance was commit `48ec056`: assert `risk_tag is None` + `family == "vulnerability"` in the two vulnerability tests; assert `bucket == "review"` + `compliant is False` in the checkpoint-analyzer test. Touched-tests local run gate:

```bash
./venv/Scripts/python.exe -m pytest tests/test_<area>.py -q --tb=line
```

---

## 🚨 Rejection-create contract: HUMAN-ONLY (2026-05-15) — TWO P0 SUB-INVARIANTS

**Hard invariant**: rejection rows are created **exclusively** when a human reviewer commits a FAIL or REVIEW verdict. AI pipeline output never creates a Rejection row — it produces a *hint* on the awaiting-review row (`tracker_aggregator._awaiting_review_row` reads it from `_ai_suggestions_for_call`) but the call stays out of the /rejections tab until a human signs off.

### Sub-invariant 1 — verdict case must be normalised

`submit_verdict` in `hitl_routes.py` MUST do `payload.verdict.strip().upper()` before the `("FAIL", "REVIEW")` membership check that gates `auto_create_rejection_for_verdict`. Frontend sends lowercase ("fail"/"review"). If the comparison reverts to case-sensitive, the entire auto-rejection branch silently skips and reviewer FAIL clicks produce nothing in `/rejections` — even though `verdict_history` saves correctly.

**Caught 2026-05-15 evening by Playwright pipeline test**: `submit_verdict` returned 200 with `auto_rejection_id: null` and the call sat in awaiting-review forever. Fix shipped in `c03e0af`.

### Sub-invariant 2 — auto-created Rejection MUST stamp confirmed_by

`auto_create_rejection_for_verdict` in `rejections_routes.py` (~line 1028) MUST set `confirmed_by=actor_id` + `confirmed_at=datetime.utcnow()` on the `Rejection(...)` constructor. The `/rejections?source=reviewer` filter is `confirmed_by IS NOT NULL`. If the constructor omits these fields, a reviewer-created rejection lands in the DB with `confirmed_by=NULL` and gets EXCLUDED by the reviewer-side filter — the human-only contract appears inverted (reviewer's row looks AI-equivalent in the UI).

**Caught 2026-05-15 evening**: rejection was created (`auto_rejection_id` populated) but absent from `/rejections?source=reviewer`. Fix shipped in `5708bcf`.

### Test-before-touching invariant

Any future change to the verdict-submit or rejection-create flow MUST be followed by:
```js
// Playwright contract test — fire on prod after each backend deploy
POST /api/calls/{id}/verdict {verdict: "fail", checkpoint_id: "cp_0"}
GET  /api/rejections?source=reviewer&limit=20
// → expected: new rejection visible with confirmed_by set
GET  /api/tracker/rows?tab=awaiting_review
// → expected: call DISAPPEARED from list
GET  /api/tracker/rows?tab=active
// → expected: call APPEARED with rejection rows
```



Authorised call sites that produce a Rejection row:
- `POST /api/rejections` (`backend/app/rejections_routes.py:391` — `Depends(require_admin)`) — operator-created.
- `submit_verdict` in `backend/app/hitl_routes.py:426` (`Depends(current_reviewer)`) → `auto_create_rejection_for_verdict` — the only production path on `verdict_action ∈ {FAIL, REVIEW}`.
- `import_xlsx_tracker.py` — CLI back-fill (operator-invoked).

**Both pipeline paths now drop `_maybe_create_rejection`**:
- `backend/app/pipeline.py:_step_finalize` — already done during 2026-05-12 taxonomy rebuild.
- `backend/app/workflows/process_call.py:_do_score` — fixed 2026-05-15 (was still calling `_maybe_create_rejection` after the asyncio path stopped).

Sanity check before any future pipeline change: grep `_maybe_create_rejection`. If it has any call site outside `backend/app/pipeline.py:1882` (the helper definition itself), you've reintroduced the bug.

## 🚨 field_sources value vocabulary invariant (2026-05-15)

When backend code stamps `Rejection.field_sources[<field>] = <source>` or `CustomerDeal.field_sources[<field>] = <source>`, the value MUST be one of the strings listed in the frontend's `TrackerFieldSource` union (`frontend-v3/src/lib/queries/tracker.ts`). Otherwise `SourceBadge` (and any other consumer that does a strict-keyed lookup) crashes the whole React tree with `Cannot read properties of undefined (reading 'bg')` and the user sees **"This page couldn't load"** on /tracker.

Real incident — 2026-05-15: `tracker_edit_routes.patch_call_meta` started stamping `"reviewer_edit"` on deal-level edits without that value existing in the frontend type / STYLES map. Page broke immediately on the next reload. Two changes shipped to prevent recurrence:

1. **Source-of-truth coupling**: the union now explicitly includes `reviewer_edit`. Any future addition needs to land in:
   - `frontend-v3/src/lib/queries/tracker.ts` → `TrackerFieldSource` union
   - `frontend-v3/src/app/(admin)/tracker/SourceBadge.tsx` → `STYLES` map (label + bg + fg)
2. **Defensive guard**: `SourceBadge` now returns `null` on unknown sources instead of dereferencing `undefined`. Backend can add new tags without bringing the page down.

**Rule of thumb**: BEFORE adding any new string to `field_sources` server-side, grep the frontend:
```bash
grep -rn "TrackerFieldSource\b" frontend-v3/src
```
and verify the consumer renders the new value defensively.

## 🚨 CI parity guardrail (2026-05-15)

GitHub Actions `coverage` workflow runs the full `pytest` suite on every push to `main`. Two recurring failure modes silently broke CI for 5 commits in a row this session:

1. **Stale test assertions after audit-driven field renames.** When a write site moves (e.g. AI narrative `Rejection.outcome_narrative → Rejection.fix_narrative`), the test that locks the old assertion fails. Cycle: code change → push → CI red → notice → fix the test → push again.
2. **Test client missing `Depends(current_reviewer)` override** when a new auth gate is added to a route. Tests get 401 instead of the asserted 200/400/404.

**Both prevented by running the touched test file before pushing.** Full gate documented in [`CLAUDE.md`](../../CLAUDE.md#ci-parity-guardrail--run-touched-tests-before-every-push) — "CI parity guardrail". Minimum:

```bash
# Touched tests first
./venv/Scripts/python.exe -m pytest tests/test_<area>.py -q --tb=line
# Full sweep before merging to main
./venv/Scripts/python.exe -m pytest -q --tb=line
```

Triggers:
- Changed `Depends(...)` on a route → re-run `tests/test_routes.py` + the route's existing test file.
- Moved which `Rejection.*` column an AI field writes to → re-run `tests/test_ai_rejection_reason.py` + `tests/test_rejection_factory*.py`.
- Added/removed fields on `TrackerRow` → re-run `tests/test_tracker_aggregator.py`.
- Wrote a new endpoint that creates `ReviewerEdit` audit rows → ensure the CHECK constraint `rejection_id IS NOT NULL OR call_id IS NOT NULL` is satisfied (every ctor passes one or both).

If CI does break:
1. `gh run list --limit 5 --workflow=coverage` → pick failed run id
2. `gh run view <id> --log-failed | tail -80` → look for `FAILED tests/...` lines
3. **Never push more commits on top of a red CI** — each adds ~7 min of build time and clouds the failure diff. Fix → re-run the specific test locally → single follow-up commit.

## 🆕 Scripts coverage gaps (2026-05-15 audit)

Full report: [[Scripts_Validation_2026_05_15]].

| # | Gap | Impact | Fix |
|---|---|---|---|
| 1 | **Valda SmartChoice script not ingested** — source PDF at `compliance-docs/Supplier Scripts/Valda SmartChoice_*.pdf` is missing from `supplier_seed.CATALOGUE`, `.planning/phase2-docs/`, and DB | Any Valda call falls through to V1/phrase-pack fallback; never graded against Valda's verbal-contract requirements | Add `Valda` to `Supplier` enum + `CATALOGUE` entry; re-run `extract_phase2_docs.py` + `seed_compliance_data --apply` |
| 2 | **`verbal_confirmation` phrase pack not in DB** — `_PACK_DEFS` declares 5 packs, only 4 ingested | Dormant: today `verbal`/`closer` segments route to supplier-specific scripts. Becomes a 0/0 hole if a supplier without a verbal script is onboarded | Run admin extractor with `stage_filter="verbal confirmation"`, save with `lifecycle_phase='verbal_confirmation'` |
| 3 | **Pack content duplication** — `Lead Generation` ≡ `Lead Generation handover/authority` (88 each, same source rows); `Confirmation callback` ≡ `Amendment call` (32 each, same source rows). 240 cps stored, 120 unique | Wastes 50% of phrase-pack storage; `passover` pack is already orphaned per `rubric_router._PHRASE_PACK_PHASE` | Optional: consolidate to 3 packs + per-pack overrides, or just document |

## ⏳ Open gaps after 2026-05-13 deploy

### 6 CI integration tests failing on `394c438`
All assertion-style mismatches against the new per-segment pipeline output; not blocking prod.

| Test | Symptom | Why |
|---|---|---|
| `test_checkpoint_analyzer::test_all_checkpoints_mixed_results` | `assert True is False` | Pre-existing — severity-bucket vs `compliant` semantic divergence from the 2026-05-11 scoring change. |
| `test_integration::test_integration_compliant_call_v2` | Expected compliant=True | V1 fallback now sets `compliant=False` whenever the analyzer summary has errors > 0; test fixture has 0 errors but the test asserts compliant on the call row, which my aggregator drops to False if any segment isn't pass. |
| `test_integration::test_integration_unknown_supplier_fallback_v1` | `assert None is not None` | Test asserts a populated field that's no longer set on this path. |
| `test_integration::test_integration_partial_checkpoint_v2` | Reason text doesn't contain 'partial' | New `_step_score` composes the reason from per-segment breakdowns ("verbal 3/4 ⚠"), not from analyzer summary's 'partial' tag. |
| `test_integration::test_integration_explicit_script_id_skips_detection` | `assert False is True` | Same as compliant_v2 — compliant=False due to aggregator. |
| `test_pipeline::test_process_call_v1_with_checkpoints` | `assert None is not None` | Same as fallback_v1. |

**Fix shape (deferred):** update each test's assertions to match the new pipeline output. ~30-60 min total. None of these break prod behavior — they pin OLD pipeline contracts.

### Phase 5 UI overhaul (a-i) still pending
Only Phase 5j (drop call_type radio from upload form) shipped. Remaining sub-tasks (≈3-4 hr total):
- 5a Queue: customer_name column, segment-list column, AI: X/N + To Review pills, hide 0% rows
- 5b Call detail: top-row pill filter (Passed/Partial/Non-Compliant), 1-click pass, loud AGENT/CUSTOMER labels, drop "needs_review" yellow, collapse to 3 verdict pills, conditional risk tags, disabled "Coming soon" email button, **new SegmentCards.tsx component**
- 5c Tracker: auto-refresh on verdict-submit, advanced filters, drop "AI" labels
- 5d Rejections: customer_name column (server already returns it via Phase 4 join)
- 5e Agents: switch to percentage metrics
- 5f Dashboard Intelligence: 4 charts + new `intelligence_routes.py`
- 5g Drop Observability entry from sidebar
- 5h Remove HelpBanners from 6 admin pages
- 5i Verify /calls catalogue route + sidebar link

### Alembic Dockerfile hides failures (latent risk)
Container starts even when `alembic upgrade head` raised — the
`|| echo 'ALEMBIC_FAILED'` swallows the exit code. The 2026-05-13
session burned hours diagnosing a 500 that traced back to a 7-day-old
silent migration failure. Future-proof by surfacing alembic failures
on `/readyz` (return 503 if last upgrade exited non-zero).

---

## 🐛 Bugs (verified 2026-05-10 audit, pre-rebuild)

### DELETE on completed calls returns HTTP 500
**Reproduced:** `DELETE /api/calls/190868a8-…` (a completed Korner Kutz call) → 500. Same endpoint on the older `failed` call `42a89a59-…` → 200.

**Root cause:** `routes.py:1525-1550` only cascades `CallCheckpoint` and the `Call`. There are 9 other tables in `models.py` with `ForeignKey("calls.id")` and **no `ondelete="CASCADE"`**:

| Line | Class |
|---|---|
| 295 | CallCheckpoint *(already cascaded manually)* |
| 363 | ReviewSession |
| 375 | VerdictHistory |
| 397 | TranscriptEdit |
| 412 | ClaimLock |
| 422 | ComplianceDecision |
| 440 | VerdictSuggestion |
| 457 | VerdictResponse |
| 506 | AgentTrace |

Failed calls don't have rows in any of these so they delete cleanly. Completed calls do, so PostgreSQL fires the FK violation on commit.

**Fix:** add `ondelete="CASCADE"` on those 9 FKs and ship a migration (see CASCADE-correct examples at lines 632/661/678/708/756/930/1028).

### Orphan customer/deal stubs after call delete
After deleting `42a89a59-…`, its parent customer `(auto-detect pending 42a89a59)` still has 1 deal and 0 calls — the Customer + CustomerDeal rows were never cleaned up. Same pattern: `(pending audio upload)` (1 deal, 0 calls).

**Fix:** in the delete endpoint, after `db.delete(call)` and re-checking, if the parent CustomerDeal has zero remaining calls → delete it; if its Customer has zero remaining deals → delete it.

### Every deal returns `stage: null`
`GET /api/deals` returns `stage: null` for every row. Per BRAIN's lifecycle doc the stage should be one of `lead_gen / closer / loa / amendment / c_call`. Either the pipeline never sets `CustomerDeal.stage` or the field is dead code. Worth tracing the Customer-Deal lifecycle path.

## High signal — fix later

### LLM occasionally extracts wrong customer name
The Passover call originally had `customer_name = "Afaq"` (which is actually the broker, mis-detected). After the Quality Agent run, it's been corrected — but **per-call** detect_names is the failure mode. Solution: add a Customer-Name Specialist Agent (single-purpose, single-call) — see [[03_AI_Pipeline/Future_Agents]].

### Empty-checkpoints scripts
**Status 2026-05-10 evening:** workaround shipped. `/api/calls/{id}/script-checkpoints` now falls back to the V1 third-party-disclosure rules when `Script.checkpoints` is empty, so the reviewer sees the actual rules the AI evaluated against (no more "Script text unavailable"). The underlying gap is still real: all 15 scripts have `checkpoints: "[]"` and the pipeline drops to V1 fallback for every call. To fix properly, the markdown extracts need to be parsed into the V2 checkpoint schema (`{section, name, required, key_phrases, customer_response_required, strictness}`), not the V1 chunk-only schema the existing seed script produces. See [[../03_AI_Pipeline/Tracker_Autofill_Plan]] / per-script V2 checkpoint authoring as a future task.

### Old transcripts don't re-label on retry
`format_diarized_transcript` only runs during Step 2 (`_step_transcribe`). On `/retry`, the cached `Call.transcript` is reused. So OLD calls that were transcribed BEFORE the speaker-label fix still show wrong labels. Workaround: clear `Call.transcript` (and `Call.word_data`) before retry to force re-transcription. Lower priority: most users will never see this since fresh uploads work correctly.

### Failed call still shows as "(auto-detect pending 42a89a59)"
The early Crosby grange call from before the OpenRouter key fix failed during pipeline and never got a customer rename. **2026-05-10: deleted in audit** — 200 OK from the API. But the parent customer + deal stubs persisted (see "Orphan customer/deal stubs after call delete" above).

## Low signal — be aware

### Vercel auto-deploys can theoretically still hijack alias
Even with the rootDirectory fix, Vercel deploys EVERY commit. Most of the time these now succeed (real ~1m builds with actual content). If anything goes back to 0ms empty, suspect rootDirectory drift first. Quick diagnose:
```bash
TOKEN=$(cat "$APPDATA/com.vercel.cli/Data/auth.json" | python -c "import json,sys;print(json.load(sys.stdin)['token'])")
curl -s "https://api.vercel.com/v9/projects/prj_eHIyIFyxusNdCd6mR9Ff469NrcKO?teamId=team_fNQJtpp1M2P2dkcoWvQIziCr" -H "Authorization: Bearer $TOKEN" | python -c "import json,sys;d=json.load(sys.stdin);print('rootDirectory:', d.get('rootDirectory'));print('framework:', d.get('framework'))"
```
Should be `frontend-v3` and `nextjs`. If not, PATCH it back.

### Local IDE shows sqlalchemy import error
Pylance/Pyright in VS Code says "Cannot find module sqlalchemy.orm" because the local Windows Python interpreter doesn't have it installed. Runtime is on Railway with `pip install -r requirements.txt` — sqlalchemy IS installed there. Ignore the IDE warning. (Don't try to "fix" it by removing the import.)

### Vercel CLI alias on Windows needs `NODE_OPTIONS=--use-system-ca`
Otherwise certificate verification fails. Already documented in [[01_Project/Deploy]].

### Manual `vercel deploy --prod` from `frontend-v3/` no longer works
After the rootDirectory fix, the CLI tries to find `frontend-v3/frontend-v3/` and fails. Run from REPO ROOT instead, or use the API-triggered deploy pattern (also in [[01_Project/Deploy]]).

## False alarms (NOT bugs)

### "Failed to connect" on `claude mcp list` for Playwright
Means the current session was started before the MCP was registered. Restart the Claude Code session — it'll connect on the next start. (Documented at [[06_Operations/Deploy_Commands]] section "MCP".)

### `deal_id=NONE` in `/api/calls?limit=10` list view
The list-view projection doesn't include deal_id. The full call detail endpoint `/api/calls/<id>` does include it. Don't panic from the list view alone.

### Agent page shows "no data" for Parat
Parat has 1 completed call but **0 dead rejections**. The agent page's main tab is "Recent flags" which sources from `dead_rejections`. Empty list is correct, just looks empty. Could add an EmptyState component for clarity.
