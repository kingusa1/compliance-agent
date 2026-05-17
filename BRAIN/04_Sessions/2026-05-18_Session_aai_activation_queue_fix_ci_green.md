---
created: 2026-05-18
updated: 2026-05-18
tags: [session, aai-activation, queue-badge-fix, ci-green, test-isolation, dependency-overrides, profile-cache]
---

# 2026-05-18 — AAI activated end-to-end + Queue Reviewed badge fix + CI both workflows GREEN

**Tip before:** `e7464c2` (BRAIN cleanup commit). **Tip after:** `edfc746` on origin/main. **7 commits + 1 Railway env var.**

User flagged three things this session:
1. "/queue Reviewed tab shows 1 in the badge but list is empty" — classic count-vs-list mismatch.
2. "Set ASSEMBLYAI_API_KEY on Railway. Do it for me." Then supplied the key.
3. "GitHub still keeps failing after we made the repo public. What is the problem?" — CI red post-public-flip.

All three resolved end-to-end. CI both workflows green for the first time this entire session.

---

## Commits shipped (most recent first)

| SHA | Title | Notes |
|---|---|---|
| `edfc746` | fix(test): wrap ReanalyzeButton tests in QueryClientProvider | Final vitest failure; mutation refactor required QC wrapper in tests |
| `c72aadc` | fix(tests): seed test-reviewer Profile in test_replay so audit_log FK passes | Last remaining pytest failure was FK violation; seed the Profile row |
| `9b8d5eb` | fix(tests): convert module-load auth overrides to autouse fixtures + invalidate profile cache between tests | test_calls_v2_shape + test_replay had module-load overrides that got cleared by the new conftest fixture; profile_cache module-level dict survived across tests and returned stale leaderboard data |
| `8f4c3b2` | fix(tests): aggressively clear dependency_overrides after every test | **Root cause of 25+ pytest failures**: the snapshot+restore pattern was capturing already-polluted state during setup and restoring the pollution on teardown |
| `796bd06` | fix(queue): Reviewed tab badge must equal list rows — drop in_review from count | + new "Reviewing" chip surfaces in_review count separately so reviewers still see what's claimed-in-progress |
| (Railway) | `ASSEMBLYAI_API_KEY=65b874e7f7f547a69cf4662d6dd1c76d` set on `compliance-agent` service | User-supplied via chat; should be rotated post-session |

All commits authored as `Mohamed Hisham <mohamedhisham735@gmail.com>` per the Vercel `COMMIT_AUTHOR_REQUIRED` gate.

---

## The three fixes — what was actually broken

### 1. Queue Reviewed-tab badge mismatch (`796bd06`)

[frontend-v3/src/app/(reviewer)/queue/page.tsx:734](compliance-agent-feat-wave5-deploy/compliance-agent-feat-wave5-deploy/frontend-v3/src/app/(reviewer)/queue/page.tsx#L734) was rendering `count={reviewedTodayCount + inReviewCount}` on the Reviewed FilterChip. But the list under `filter=today` (aka `reviewed_today` after wire-boundary mapping at [lib/api.ts:198](compliance-agent-feat-wave5-deploy/compliance-agent-feat-wave5-deploy/frontend-v3/src/lib/api.ts#L198)) only returns calls with `review_status='reviewed'`. So `in_review` calls (claimed but not submitted) inflated the badge without appearing in the list.

Fix: `count={reviewedTodayCount}` (badge equals list). When `inReviewCount > 0`, a new "Reviewing" FilterChip surfaces the in_review count separately, click-routes to the All tab.

Verified live on prod: `Pending: 10`, `Reviewed: 0` (matches `reviewed_today=0`), `Reviewing: 2` (matches `metrics.in_review=2`). Structurally impossible for the original "badge=1, list=0" mismatch to recur.

### 2. AssemblyAI key activation (no code change — Railway env var)

`railway variables --service compliance-agent --set "ASSEMBLYAI_API_KEY=65b874e7..."` set the key. After Railway redeployed, triggered retry on call `c9b3f559`:

- Status: `needs_manual_review` (force-review gate fired correctly)
- DG transcript: 848 words (1 speaker — bad diarization)
- AAI transcript: 877 words **(2 speakers — proper diarization)**
- Diarization selector picked AAI (because DG only had 1 speaker)
- Cross-validation: **82.38% agreement**, below 0.85 floor
- 8 disagreement samples captured

**The user-reported "transcript only showed the agent" bug is resolved.** AAI's 2-speaker diarization is now feeding `call.word_data`, so the transcript player renders proper Agent + Customer turns. Chip flipped from grey "AssemblyAI transcript missing" → amber "⚠ Transcription divergence: 82% agreement (floor 85%) DG 848 · AAI 877 ▼" with the side-by-side disagreement drawer working.

### 3. CI test workflow GREEN (commits `8f4c3b2`, `9b8d5eb`, `c72aadc`, `edfc746`)

After the repo went public, Actions ran for the first time in days. The `test` workflow had 25+ pytest failures + 3 vitest failures. Triaged in waves:

#### Wave A: dependency_override leakage (the big one)

[backend/tests/conftest.py](compliance-agent-feat-wave5-deploy/compliance-agent-feat-wave5-deploy/backend/tests/conftest.py)'s `_reset_dependency_overrides_after_test` autouse fixture used **snapshot + restore**. Pytest fixture ordering meant the snapshot ran AFTER the test file's autouse `clean_db` had already installed `app.dependency_overrides[get_db] = _override_get_db` (pointing at a private in-memory SQLite). The "restore" put the override BACK on teardown → the override leaked into subsequent test files that needed real Postgres (test_audit_coverage et al.) or that asserted 401 from unauthenticated requests.

Fix: switched to **aggressive clear on teardown**. Every test file's own autouse `clean_db` reinstalls the override it needs on setup; teardown wipes the slate. Local: 46/46 of the previously-failing tests passed after this single change.

#### Wave B: module-load auth overrides

`test_calls_v2_shape.py` and `test_replay.py` installed `app.dependency_overrides[current_reviewer]` at MODULE LOAD TIME (not inside a fixture). After Wave A, the aggressive clear stripped these overrides on the first test, then the second test ran with no override → 401.

Fix: converted both to autouse fixtures so the override gets re-installed on every test setup.

#### Wave C: profile_cache module-level state

`app.profile_cache._PROFILE_CACHE` is a module-level dict with TTL. First test to call `get_profile_names()` populated it from THAT test's private SQLite. Test_queue ran next, re-seeded Profile rows in its own SQLite, but the cache returned stale data → leaderboard showed IDs (`'mo'`) instead of names (`'Mo Ibrahim'`).

Fix: added `invalidate_profile_cache()` to conftest's autouse teardown.

#### Wave D: audit_log FK violation in test_replay

After Wave B fixed test_replay's auth, the `reanalyze` route writes an audit_log row stamped with `actor_id="test-reviewer"`. The `audit_log.actor_id_fkey` requires a Profile row with that id.

Fix: my autouse fixture in test_replay now seeds the `test-reviewer` Profile if absent.

#### Wave E: ReanalyzeButton vitest — QueryClientProvider missing

`ReanalyzeButton` was refactored to use `@tanstack/react-query`'s `useMutation`; the 3 unit tests still rendered the component without a `QueryClientProvider` in the tree → "No QueryClient set" throw.

Fix: added a `renderWithQueryClient` helper that wraps every render. Local: 3/3 pass.

---

## CI evidence (final state, tip `edfc746`)

- `coverage` workflow: **success** ✅
- `test` workflow: **success** ✅ (pytest + vitest + skip-without-label playwright)

First commit this entire session where every check is green. The "GitHub still keep failing" complaint is resolved at the source — no skip-tests, no continue-on-error masks.

---

## Continuous-learning rules captured

1. **`dependency_overrides` snapshot+restore is a footgun.** When the autouse fixture's setup runs AFTER test-file fixtures (alphabetical ordering puts `_reset...` first but pytest doesn't always honour this between conftest vs test-file), the snapshot captures already-polluted state and "restores" the pollution. Aggressive clear is safer; let each test file's own setup reinstall what it needs.

2. **Module-level state in app modules will survive across tests.** `_PROFILE_CACHE`, `_jwks_client`, any singleton-pattern cache. Conftest's autouse teardown should call the explicit invalidate function for every such cache. Audit pattern: `grep -rn "^_[A-Z_]* = {}\\|^_[A-Z_]* = None\\|@lru_cache" app/`.

3. **Module-load `app.dependency_overrides[...]` writes are deceptive.** They look like they "install once at import" but if anything clears `dependency_overrides` (which tests do), they don't reinstall. Always wrap in `@pytest.fixture(autouse=True)`.

4. **`audit_log.actor_id` FK to `profiles.id` propagates into test setup.** Any test that hits a mutating route under a fake reviewer identity must seed a matching Profile row. Worth adding a generic `_fake_reviewer_profile` fixture to conftest.

5. **`@tanstack/react-query` mutation refactors require test-side `QueryClientProvider`.** When a component switches from raw `fetch` to `useMutation`, the existing unit tests will throw "No QueryClient set". Audit pattern: `grep -rn "useMutation\|useQuery" frontend-v3/src` → check that every component under test has a wrapper in its `.test.tsx`.

6. **Badge counts MUST equal list rows.** When the badge predicate diverges from the list predicate (Pending = unclaimed only, but Pending badge included in_review; Reviewed = reviewed_today only, but Reviewed badge included in_review), reviewers lose trust. Either align both predicates or add a separate badge/chip for the second concept. The Queue page now has Pending / Reviewing / Reviewed all distinct.

7. **Vercel `COMMIT_AUTHOR_REQUIRED` + GitHub credential auto-switch is a recurring footgun.** Every `git push` must be preceded by `gh auth switch -u kingusa1`, and every commit needs `-c user.email=mohamedhisham735@gmail.com`. The `sheerazfame` token can't see `kingusa1/compliance-agent` and fails with "Repository not found." Already documented in CLAUDE.md.

---

## 🚨 Still recommended user actions (defence-in-depth)

1. **Rotate the OpenRouter key** at https://openrouter.ai/settings/keys (revoke `sk-or-v1-fcd5f2d5...`, set new on Railway). The 2026-05-18 history rewrite removed it from origin/main but any clone made while the repo was private still contains it.

2. **Rotate the AssemblyAI key** at https://www.assemblyai.com/app/account/api-keys (revoke `65b874e7...`, set new on Railway). The key passed through chat history during this session.

Both 30-second tasks. Without rotation, the leaked values are still valid for unauthorized API calls billed to Mohamed's account.
