---
created: 2026-05-10
updated: 2026-05-27
tags: [state, issues, gotchas, d9-widening, lag-fix, max-config, n-a-vocabulary, upload-drops, residual-lag]
---

# Known issues / gotchas

## 🚨 2026-05-27 — Open carry-forward after enterprise max-out config wave

Today shipped D9 widening (supplier-peel re-raise) + LAG fix (off-loop file reads for 5 transcribers) + max-out pool/retry/anyio config (pool 30/60, STEP_RETRY 5, anyio 400, pool_timeout 20s) on a Railway 24 vCPU / 24 GB Pro replica. Soak test under new config: **0 of 7 calls failed** (vs 7 of 10 failed yesterday under pool 10/20). What's still open:

### D10 (CRITICAL, OPEN) — AI verdict accuracy ~21 % wrong
Per the 2026-05-26 analyst report (in 2026_05_26_Session_compliance_status_aggregation_fix.md), pattern 1 (n_a vocabulary missing) alone removes ~16 phantom failures per call. Schema + analyzer prompt + score math + frontend chip + tests. Designed in detail in 2026_05_28_Resume_Prompt.md and 2026_05_27 session log.

### D13 (MEDIUM, NEW) — Upload modal drops 2-3 of 10 files under rapid sequential drops
Yesterday's 10-upload soak created 3 orphan "(pending audio upload)" deal stubs without Call rows. Repro: upload 10 files in rapid 4-round sequence (1+3+3+3). Likely race condition in `BatchUploadModal.tsx` between the stub-deal POST and per-file POST.

### D14 (LOW, NEW) — Residual loop_lag ~1.5s under bursts
Off-loop file reads dropped lag from 13393ms → 1469ms but it still fires. Investigate sync CPU paths in `checkpoint_analyzer.py` batch dispatch — likely json.loads/dumps on multi-KB LLM responses + fuzzy_match Levenshtein code. Route through `anyio.to_thread.run_sync`.

### D1 (HIGH, OPEN) — Customer-name divergence Call (person) vs Deal (business)
Unchanged from yesterday. NAME_PROMOTE skip logic deferred.

### D2 (MEDIUM, partial closure) — BUSINESS_DETECT full-TA name
Today's Round 1 returned full "Mrs. Zoe Larkins Trading As Corner Cuts" — looks closed but needs 3-5 more samples to confirm.

### D4 (MEDIUM, OPEN) — Score volatility across runs
Today's Zoe Larkins re-run scored 3/26 (yesterday 2/26 + 3/26 between runs). Still in the ~50 % variance band.

### D6 (HIGH, mitigated) — SSE per-call fan-out gap
Unchanged; 3s poll fallback covers reviewer-facing symptom.

---

## 🚨 2026-05-26 — Defects after the D5/D7/D8/D9 fix wave (HISTORICAL)

Today's PM session closed 5 reviewer-facing defects (D5 UI auto-refresh, D7 needs_manual_review terminal, D8 words mid-pipeline 404, D9 statement_timeout bulk crash, plus the chronic CI reds D11+D12). What's still open going into the next session:

### D1 (HIGH, open) — Customer-name divergence between Call and Deal
`Call.customer_name` captures the PERSON detected on the call (e.g. "Zoe Larkins", "Nikki"); `Deal.customer_name` captures the BUSINESS via BUSINESS_DETECT (e.g. "Corner Cuts"). Reviewers see two different "customer" values on the call detail vs the customer/deal pages. The cleanest fix is to add `Call.business_name` as a separate column and consistently render both, OR force BUSINESS_DETECT to return the full "Mrs Zoe Larkins T/A Corner Cuts" string and use it everywhere.

### D2 (MEDIUM, open) — BUSINESS_DETECT regression on full trading-as name
Prior session returned `"Mrs Zoe Larkins Trading As Corner Cuts"`; today returned only `"Corner Cuts"`. Same audio. Transcript ground-truth confirms the agent reads the full "Mrs. Zoe Larkins, trading as Corner Cuts" verbatim. The prompt is regressing on completeness. Likely related to D10.

### D3 (MEDIUM, fixed via manual sweep, scheduled cleanup pending)
`/api/admin/sweep-orphans` cleans orphan deal stubs. No scheduler invokes it; the route works but is unscheduled. Easy ticket: register it as an Inngest cron or as a startup hook (with a > 1 h age filter).

### D4 (MEDIUM, open) — Score volatility across runs on identical audio
Same `c call.mp3` scored 3/26 in one run and 2/26 in the next. ~50 % variance. Confounds reviewer trust. Likely the LLM sampling — needs temperature pin + cache by audio hash.

### D6 (HIGH, mitigated via poll fallback) — SSE per-call fan-out gap
`useCallEvents(call_id)` opens an EventSource against `/api/calls/{id}/events` — connection succeeds, subscriber row registers in `_SUBSCRIBERS`, but `realtime.publish(call_id, ...)` calls don't deliver to the per-call queue in ~15 % of cases. Asyncio fan-out path in `realtime.publish` is suspected. Workaround in production: 3 s safety-net poll on every in-flight call (b457d85 + 4af7754). Deep dive deferred — symptom is masked.

### D9 (HIGH, fixed 211c299 + 4065e18) — Validation pending
Three commits shipped (db_retry extension + `_trace_step` jittered retry + 11 new tests). The previously failed `31b4af9d` passover.mp3 was uploaded before the fix; the fix only protects new uploads. **Next session: bulk-upload Little Dowran files + Clifton mix + Zoe Larkins files at concurrency 5-10 and confirm zero `status="failed"` outcomes plus presence of `STEP_RETRY` log lines under contention.**

### D10 (CRITICAL, open — analyst report 2026-05-26 morning) — AI verdict accuracy ~21 % clearly wrong
Analyst agent sampled 523 checkpoints across 8 calls. Aggregate accuracy estimate: ~66 % correct, 13 % questionable, 21 % clearly wrong. Five root-cause patterns:

1. **"if applicable" conditional checkpoints marked `fail`** instead of `n_a` when the conditional doesn't apply (~8 phantom medium failures per verbal segment)
2. **Negative-prohibition checkpoints marked `fail`** when the prohibited behaviour simply didn't occur (~77/88 failures on a 20s lead-gen segment)
3. **Untriggered conditional behaviours** inconsistently graded
4. **Inconsistent absence-of-evidence treatment** between identical situations
5. **Misclassified segments** — call 3f06a227 LOA file scored against verbal rubric → 25 phantom fails

Fix path:
- Add `n_a` to the verdict vocabulary (schema CHECK constraint, scoring math `score = passed / (total - n_a)`, frontend rendering)
- Tag checkpoints with `check_type: positive_obligation | negative_prohibition | conditional_obligation`
- Update checkpoint_analyzer.py prompt with type-aware defaults

Highest-leverage single fix: pattern 1 alone eliminates ~16 phantom failures per call.

### D11 (CI chronic, fixed e745147) — `test_recycle_under_supavisor_kill_window`
Rebaselined `<= 600` → `<= 1800` to match the morning's intentional pool_recycle bump.

### D12 (CI chronic, fixed e745147) — `test_logged_step_emits_workflow_step_logs_on_success`
Test handler level bumped INFO → DEBUG to match the 2026-05-24 production demotion of the start-of-step log line.

---

## 🚨 2026-05-25 — Vercel GitHub App NOT installed on `kingusa1/compliance-agent` → auto-deploy is silently broken

**Symptom:** five `git push origin main` from this session (`cb299a0`, `340cd74`, `a4adf15`, `eb07e73`, `c49b1df`) all succeeded against `kingusa1/compliance-agent` but none triggered a Vercel deployment. Vercel's latest production deploy on the project was 1 day old (`7b6d8883`, by `bbm-group`) until I triggered a manual REST deploy at 2026-05-25 ~22:55 UTC.

**Diagnosis:**
- `gh api repos/kingusa1/compliance-agent/hooks --jq '. | length'` → **0** (no classic webhooks).
- `gh api repos/kingusa1/compliance-agent/installation` → no Vercel GitHub App installation visible on the repo.
- Vercel project link is correct: `prj_eHIyIFyxusNdCd6mR9Ff469NrcKO` → `kingusa1/compliance-agent` `main`, `gitProviderOptions.createDeployments=enabled`, `paused=null`. The Vercel side is fine. The GitHub side has no app to send the push event to.

**Workaround used today** — manual REST API deploy via the personal token in `~/.secrets/vercel.env`:

```bash
VERCEL_TOKEN=$(grep VERCEL_TOKEN ~/.secrets/vercel.env | cut -d= -f2 | tr -d '"\'')
curl -ks -X POST \
  "https://api.vercel.com/v13/deployments?teamId=team_fNQJtpp1M2P2dkcoWvQIziCr&forceNew=1" \
  -H "Authorization: Bearer $VERCEL_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"compliance-agent","project":"prj_eHIyIFyxusNdCd6mR9Ff469NrcKO","gitSource":{"type":"github","ref":"main","repoId":"1233382040"},"target":"production"}'
```

Returns `dpl_*` immediately; polls READY in ~30s; auto-aliases `compliance-agent-mu.vercel.app`. Confirmed working — today's deploy `dpl_7qMz1drv7KVfySGDb5Bp3o5YnNGG` shipped `c49b1df` to prod that way.

**Owner action required (one-time):**
1. Open https://github.com/apps/vercel/installations/new
2. Pick the `kingusa1` account, grant access to `kingusa1/compliance-agent` (or "all repositories")
3. After install, a webhook delivery should appear in
   `https://github.com/kingusa1/compliance-agent/settings/installations` (only the user can see this).
4. Re-verify with: `gh api repos/kingusa1/compliance-agent/installation` (should return install id + app_slug=`vercel`).

Until that's done, every push needs the manual REST API curl above. The token is good through whenever Vercel rotates it (verified 2026-05-23 via `GET api.vercel.com/v2/user`).

---

## ✅ 2026-05-24 — /customers showed mixed-supplier phantom customer (FIXED commit `42345d0`)

**Owner-reported:** Opening `/customers/(pending-audio-upload)` showed **5 unrelated deals** (E.ON Next + British Gas + 3 placeholder-supplier deals) merged into ONE customer row with a 47-risk-tag rollup. Header rendered "British Gas" + "Opener · Closer · British Gas LOA via DocuSign" but DEAL-9977 was clearly E.ON Next.

**Root cause:** `POST /api/deals/stub` (`routes.py:578`) writes the literal string `"(pending audio upload)"` into `customer_deals.customer_name` as a deal-stub placeholder before pipeline runs. `customers_routes.py:_LIST_SQL` groups by `LOWER(TRIM(customer_name))` with only `WHERE customer_name IS NOT NULL` as the filter — every stub deal collapsed into a single synthetic customer slug `(pending audio upload)`. That synthetic row aggregated suppliers/agents/risk tags across deals that had nothing to do with each other.

**Fix (commit `42345d0`):**
- New helper `_real_name_predicate(expr)` returns a SQL fragment excluding NULL / empty / `"(pending audio upload)"` / `"(no customer)"` / `"Untitled"` / `"(auto-detect pending..."`. Mirrors the frontend `isPlaceholderCustomerName` set in `lib/customer.ts`.
- Applied to all 7 aggregation surfaces in `customers_routes.py`: list, detail deals, rollup base, rollup recurring-issue, rollup fix-directives, rollup risk-tag flags, timeline (uses COALESCE shape).
- Partial index `ix_deals_real_customer_name` on `LOWER(TRIM(customer_name))` with the same predicate (migration `2026_05_24_cust_idx`) — Index-Only Scan instead of seq scan at 2k+ rows.

**Effect:** Placeholder deals stay visible on `/deals` + `/tracker` (their natural home) until a reviewer/AI sets a real name. Then the tracker side-panel `customer_name` dual-write puts the deal onto the right customer page automatically.

---

## 🚨 2026-05-24 — Always push as `kingusa1`, NEVER as `bbm-group` (Windows credential helper bug)

The Windows credential helper on this machine flips the active GitHub identity back to `bbm-group` mid-session — sometimes between two pushes in the same session. When it happens, `git push` returns:

```
remote: Permission to kingusa1/compliance-agent.git denied to bbm-group.
fatal: ... The requested URL returned error: 403
```

**ALWAYS run `gh auth switch --user kingusa1` BEFORE every `git push`** — even when a prior push in the same session succeeded as `kingusa1`. The flip is silent.

Owner-visible failure mode: a `bbm-group` push attributes the commit to `bbm-group` on Railway/Vercel + on the GitHub commit author. Owner asked on 2026-05-24: *"in the future when you start pushing to GitHub for cell railway, don't push by BBM Group. You should push by Kingusa1."*

The local repo's `user.name=kingusa1` + `user.email=IT@bbmgroup.io` is NOT load-bearing for this bug — `git push` uses the credential helper's identity, not the local git config. `gh auth switch` is the only fix.

Durable feedback memory: `~/.claude/projects/c--/memory/feedback_compliance_agent_push_identity.md`.

---

## 🆕 2026-05-24 PM — `POST /api/admin/backfill-compliant-strict` shipped; awaiting one prod run

Endpoint registered on prod (Railway running `a33b66e`). DevTools one-liner from the admin user's browser:

```js
await fetch("/api/admin/backfill-compliant-strict", { method: "POST", headers: { Authorization: "Bearer " + (await window.supabase.auth.getSession()).data.session.access_token } }).then(r => r.json())
```

Cleans the 7 stale Compliant tab rows from the pre-`ac383ba` lax rule. Idempotent. Returns `{flipped, to_pending, to_non_compliant, scanned_segments}`.

---

## ✅ 2026-05-24 — `/tracker` Compliant tab shows non-compliant rows

> **User screenshot (2026-05-24 evening):** Compliant tab on prod tracker shows 7 rows, but 5+ of them are clearly NOT compliant. Score column on those rows:
> | Customer | Agent | Score | % |
> |---|---|---|---|
> | (pending audio upload) | Cade Tandy | 3/20 | 15% |
> | Crosby Grange Properties | Paris | 37/88 | 42% |
> | Bob's Glazing Limited | Sam Escrich | 19/26 | 73% |
> | **Bob's Glazing Limited** | **Sam** | **9/11** | **82%** ✓ |
> | Clifton Rest Home Association | Bradley Clayton | 8/11 | 73% |
> | Awais | Ethan | 7/11 | 64% |
> | Awais | Ethan | 14/25 | 56% |
>
> Only ONE of the 7 rows (Sam, 9/11 = 82%) is actually ≥ 80% threshold. The other 6 are below — but they're rendering on the Compliant tab.

**Root cause hypothesis** (not yet verified — to investigate next session):

`backend/app/tracker_aggregator.py:625-637` (the `compliant` branch of `build_tracker_rows`) probably matches calls where:
- `Call.compliance_status == "compliant"` (AI marked it compliant) OR
- A weak fallback like "has score and no Rejection rows"

That second condition wrongly matches calls where the AI marked the call `pending` / `needs_manual_review` AND no Rejection row exists yet. The 2026-05-23 BRAIN note already mentions a fix attempt: *"was 'any completed call with no rejection', but after the 2026-05-12 reviewer-initiated-only switch, EVERY call qualifies until a reviewer files a Rejection — so the tab showed all 14 calls instead of the 5 the AI actually marked compliant."* — the fix landed but apparently isn't complete or regressed.

**Where to look first:**
- `backend/app/tracker_aggregator.py:625` — the `if tab == "compliant":` query — what's the filter? `Call.compliant.is_(True)`? Or a status check that's too lax?
- `backend/app/models.py:Call.compliant` — true / false / null semantics
- `backend/app/pipeline.py:_step_finalize` — when does `call.compliant` get set true vs left null

**Quick verify command** (next session): query the actual rows on the Compliant tab via the API:
```
curl -sS https://compliance-agent-production-690e.up.railway.app/api/tracker/rows?tab=compliant -H 'Authorization: Bearer ...'
```
and check what `compliance_status` / `compliant` field each row carries.

**Impact:** Reviewer can't trust the Compliant tab. They'd open it expecting clean calls and find 6/7 failures. Until fixed, they should use the Active / Awaiting review tabs as the source of truth and treat Compliant as "approximate".

**Related:**
- [[../04_Sessions/2026_05_24_Session_wiring_fix_wave]] — earlier session that touched the tracker
- Live_State note on 2026-05-23 tracker filters fix

---

## 🆕 2026-05-18 — Pre-existing pytest test-workflow failures (surfaced once Actions unblocked)

When the repo went public + Actions started running again, the `test` workflow's pytest job revealed many failing tests that pre-date this session. The `coverage` workflow (touched-files + 50% gate) is GREEN because it skips full pytest unless backend Python files changed; the `test` workflow runs `pytest -v --tb=short` unconditionally and catches all the pre-existing failures.

Failing tests (sample):
- `test_agent_trace.py::test_get_trace_requires_auth`
- `test_audit_coverage.py::test_edit_metadata_writes_audit`
- `test_audit_coverage.py::test_hitl_claim_release_writes_audit`
- `test_claim.py::test_claim_creates_session_and_lock` (+ 3 siblings)
- `test_compliance_lists.py::test_without_auth_401`
- `test_compliance_override.py::test_*` (×4)
- `test_customer_email.py::test_401_without_auth`

All auth-related (401 vs expected 200/404). Pattern documented in this file under "CI parity guardrail" — tests get 401 when `Depends(current_reviewer)` is added without test-side `app.dependency_overrides`.

Fix recipe is per-test in CLAUDE.md, but applying it everywhere is a separate cleanup session. Tracker can park these because:
- Coverage workflow GREEN (the meaningful gate)
- All failures are pre-session — none introduced by 2026-05-17/18 work
- Vitest also has 3 pre-existing `ReanalyzeButton` failures (missing `QueryClientProvider` wrapper) — same status

## 🆕 2026-05-18 — Alembic migration must guard against vanilla Postgres

`2026_05_16_rls_realtime` was failing CI's `alembic upgrade head` because it references `auth.uid()` and `supabase_realtime` publication — both Supabase managed-Postgres primitives. Fixed in `2c929b4`: `upgrade()` now detects the auth schema + realtime publication at start and bails gracefully when missing.

**Future RLS migrations** must follow the same pattern. Template:

```python
def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return  # SQLite tests

    has_auth = bind.execute(
        sa.text("SELECT 1 FROM information_schema.schemata WHERE schema_name = 'auth'")
    ).first() is not None
    if not has_auth:
        return  # Vanilla Postgres (CI, self-hosted)

    # ...rest of migration referencing auth.uid() etc.
```

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

---

## 🚨 Human-review pipeline is cosmetic (2026-05-16)

**Full evidence + fix sequence: [[../04_Sessions/2026-05-16_Session_queue_human_review_audit_verification]]**

The aggregate-verdict Submit button is a prototype that `console.log`s a payload and never calls `POST /api/calls/{id}/verdict`. Backend endpoint exists and works at `hitl_routes.py:426`; frontend mutation `useSubmitVerdict()` exists at `reviewer.ts:203` but `VerdictTab.handleSubmit` doesn't import it.

**Downstream symptoms that collapse the moment this is fixed:**
- Reviewed tab on `/queue` stays at 0 (per-CP review never sets `reviewed_at`/`reviewed_by`; only `submit_verdict` does).
- `/rejections` Active tab is permanently empty (`auto_create_rejection_for_verdict` never runs).
- Compliant / Non-compliant pages show AI scores as if they were reviewer outcomes.
- Sidebar "Human Review Queue · 4" badge never decrements.

**Adjacent unwired flows on the same page:**
- `useClaimCall()` / `useReleaseCall()` defined but never invoked — yellow "Reviewing" badge is cosmetic.
- `require-double-review` + `GET /api/reviewers` orphaned backend-side.

**Audit corrections (do NOT chase these):**
- Per-CP review notes ARE persisted (mutation passes `?notes=...`, backend writes both `checkpoint_results` JSON + DB row at `routes.py:818,843,852`).
- Queue tab→filter mismatch (`filter=today` → 422) was fixed at the wire boundary in [[../04_Sessions/2026-05-16_Session_six_hour_run]] commit `3ecd34c`.
- Pending tab first-paint bug: not a state-binding bug — `useQueueQuery(filter)` is correctly bound.

## 🐛 Tracker CATEGORY pill filters are decorative (2026-05-16)

Pills (Admin error / Process failure / Verbal sales err / Compliance issue / etc.) apply orange highlight on click but **the table does not re-filter in real time**. They "wake up" only when another input changes (search box, MPAN box), at which point the previously-selected pill suddenly filters retroactively, creating phantom "0 rows" results while the tab badge still says `Awaiting review · 4`. The `Clear` button only clears the More-filters chips, not the CATEGORY pill. The More-filters chips (Supplier / Agent / Status / Verdict) DO work correctly.

## 🐛 Edit-metadata modal silently corrupts customer names (2026-05-16)

Modal pre-fills `customer_name` with the first token of the canonical name (`"Awais Mustafa Ta Charles Palace"` → `"Awais"`). Reviewer who clicks Save without touching the field destroys the canonical name. `Sales agent` field pre-fills with placeholder `"Sammy R."` regardless of the persisted agent, so a Save will overwrite the real agent (`"Ethan"`) too. Backend endpoint at `routes.py:2945-3014` has no length validation and no "don't overwrite with placeholder" guard.

## 🐛 Rejections Fixed / Dead / Archive tabs stuck on "Loading rejections..." forever (2026-05-16)

Active tab loads (empty, per the human-only contract). Fixed / Dead / Archive sub-tabs spin the skeleton forever — fetch likely returns 200 + empty array but the loading-state machine doesn't transition on empty result. Not yet inspected at code level.
