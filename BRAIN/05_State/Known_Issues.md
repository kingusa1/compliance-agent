---
created: 2026-05-10
updated: 2026-05-15
tags: [state, issues, gotchas]
---

# Known issues / gotchas

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
