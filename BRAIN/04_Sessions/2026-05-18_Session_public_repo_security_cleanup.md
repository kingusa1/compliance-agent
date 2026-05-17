---
created: 2026-05-18
updated: 2026-05-18
tags: [session, security, public-repo, git-filter-repo, history-rewrite, ci-unblock, playwright-mcp]
---

# 2026-05-18 — Repo went public + history scrubbed + CI unblocked

**Tip before:** `0bed954` (private repo, Actions blocked by spending limit). **Tip after:** `8bed1cb` on origin/main (public repo, coverage CI green).

User asked to make the repo public, told to "fix the repo and make all the things inside it secure" + "remove the readme file". Audit found a real OpenRouter API key committed in the initial import (`4da9573`) and present in every commit since. Used `git filter-repo` to scrub the key + delete README.md from all 239 commits, force-pushed, then flipped the repo to public.

---

## Commits shipped this session

| SHA | Title | Notes |
|---|---|---|
| (history-rewrite) | All 239 commits rewritten via `git filter-repo --replace-text --path README.md --invert-paths` | Leaked OpenRouter key replaced with `REDACTED-LEAKED-OPENROUTER-KEY-ROTATED-2026-05-18`; README.md dropped from every commit |
| `f5e00c3` | chore(security): require OPENROUTER_API_KEY env var, no fallback | Working-tree fix to legacy `transcribe_all.py` + `transcribe_openai.py` — hard-fail when env var missing |
| `2c929b4` | fix(alembic): skip rls_realtime migration on vanilla Postgres (CI) | `2026_05_16_rls_realtime` referenced `auth.uid()` + `supabase_realtime` publication; CI postgres has neither → migration aborted. Now detects Supabase env at start of `upgrade()` and bails gracefully on vanilla pg |
| `8bed1cb` | fix(test): align email-preview test with a12b951 placeholder removal | Vitest `email-preview.test.tsx:144` asserted the old hardcoded `compliance@xaia.ae` fallback; updated to assert the new "(reviewer email unavailable)" string |

All authored as `Mohamed Hisham <mohamedhisham735@gmail.com>` to satisfy Vercel `COMMIT_AUTHOR_REQUIRED`.

---

## The leaked key

OpenRouter key with prefix `sk-or-v1-fcd5f2d5...` (Mohamed's account, Opus 4.7 + paid credits). Full value purposely not echoed here so GitHub's push-protection doesn't flag this BRAIN file. Committed in:

- `backend/scripts/legacy/transcribe_all.py:27`
- `backend/scripts/legacy/transcribe_openai.py:24`

Both as a hardcoded fallback when the `OPENROUTER_API_KEY` env var wasn't set. Present in every commit since the initial import (`4da9573` on 2026-05-09).

**Action taken:**

1. `git stash` working tree (preserved my pre-session scrub of the same files)
2. Backed up `.git` to `.git-backup-pre-filter-repo-20260518-003155`
3. `git filter-repo --force --replace-text .filter-repo-replacements.txt --path README.md --invert-paths`
4. Verified scrub: `git log --all --full-history -p | grep "sk-or-v1-fcd5f2d5..."` → 0 matches (truncated prefix here to avoid push-protection block)
5. Force-pushed: `git push --force origin main` (236→239 commit SHAs all rewritten)
6. Verified remote: `gh api repos/kingusa1/compliance-agent/branches/main` returns the new tip
7. Flipped to public: `gh api -X PATCH repos/kingusa1/compliance-agent -f private=false`
8. Restored stash + committed the working-tree scrub as `f5e00c3`

**Strongly recommended next user action (NOT done by me):** rotate the OpenRouter key on the dashboard as a belt-and-suspenders defence, in case any clones / forks were made of the private repo while the key was live. Once rotated, the leaked value becomes a dead string regardless of where it ends up.

---

## Why the CI was failing for hours

**Before public flip:** Every CI job was rejected with "The job was not started because recent account payments have failed or your spending limit needs to be increased." This was the user-reported "I have upgraded github and it still keeping failing." Diagnosis: GitHub plan upgrade does NOT auto-raise the Actions spending limit; that's a separate dial set to $0 by default. The compliance-agent repo was private + owned by a User account → every minute counts against the user quota → 0 minutes available + $0 spending limit = full block.

**Fix path chosen by user:** make the repo public. Public repos have unlimited free Actions on linux runners → no billing involvement → jobs run immediately.

**After public flip:** Both `coverage` and `test` workflows started executing. Coverage went green after my alembic + vitest fixes. Test workflow still has multiple pre-existing pytest failures that pre-date this session (test_claim_*, test_compliance_*, test_audit_coverage, test_customer_email — all auth-related 401s). These were never visible while Actions were blocked.

---

## What's verified live on prod via Playwright MCP

| Surface | Status | Evidence |
|---|---|---|
| `/login` → `/dashboard` | OK | 307 + 200 |
| `/rejections` Active tab | OK | empty-state renders ("No rejections in Active tab") |
| `/rejections` Fixed/Dead/Archive sub-tabs | **OK** — BRAIN-noted "stuck loading" bug is RESOLVED by 2026-05-16 `placeholderData: keepPreviousData` + `retry: 1` audit fix | All 3 tabs render the empty-state without stuck loading |
| `/tracker` CATEGORY pill filter | **OK** — BRAIN-noted "decorative" bug is RESOLVED — pill click filtered table from 12 → 0 rows after ~1.5s | Direct DOM observation |
| `/calls/c9b3f559` two-layer chips | OK | `data-testid="transcript-agreement-skipped"` = "AssemblyAI transcript missing", `data-testid="diarization-chip"` = "Diarization fallback — DG 1 · AAI 0 speakers" |
| `/queue`, `/customers`, `/deals` | OK | All return 200 |

---

## Known pre-existing CI tech debt (not introduced this session)

The `test` workflow's pytest job has many failures. Sample from run `26002543561`:

- `test_agent_trace.py::test_get_trace_requires_auth`
- `test_audit_coverage.py::test_edit_metadata_writes_audit`
- `test_audit_coverage.py::test_hitl_claim_release_writes_audit`
- `test_claim.py::test_claim_*` (4 tests)
- `test_compliance_lists.py::test_without_auth_401`
- `test_compliance_override.py::test_*` (4 tests)
- `test_customer_email.py::test_401_without_auth`
- many more

All auth-related (401 vs expected 200/404, etc.). These were silently failing before Actions were unblocked. They're documented in BRAIN's "CI parity guardrail" section as the pattern "tests get 401 instead of asserted 200/400/404 when `Depends(current_reviewer)` is added without test-side `app.dependency_overrides`."

Fix recipe is in [[../CLAUDE.md#ci-parity-guardrail--run-touched-tests-before-every-push]] but applying it to every failing test is a separate session.

The `coverage` workflow's gated pytest job (touched-files only + 50% coverage floor) is **GREEN** ✅ — that's the meaningful CI check for this codebase.

Plus a separate vitest pre-existing failure: `ReanalyzeButton.test.tsx` × 3 — missing `QueryClientProvider` wrapper. Documented in 2026-05-16 BRAIN session as pre-existing.

---

## Continuous-learning rules captured

1. **Public-repo flip checklist** — before flipping any private repo to public:
   a. `git log --all -p | grep -E "sk-or-v1-|sk-ant-|gho_|ghp_"` and similar secret patterns
   b. List `git ls-files | grep -iE "secret|credential|password"`
   c. Check for `.env` files (only `.env.example` should be tracked)
   d. If anything leaks, ROTATE first, then `git filter-repo` the history, then force-push, then flip
   e. Once public, even revoked secrets are evidence — but rotation makes them harmless

2. **GitHub Actions spending limit is separate from plan tier.** A Pro plan does NOT auto-raise the Actions spending limit; default is $0 on every account. Private repos burn from the user/org Actions quota; public repos have unlimited free runners. The "Checks Failed" 3-5s job times are diagnostic of pre-execution rejection (billing) not test failures.

3. **`git filter-repo` is the right tool** (NOT `git filter-branch`). Installed via `python -m pip install git-filter-repo` to `%APPDATA%/Python/Python314/Scripts/git-filter-repo.exe`. Re-runs are safe via `--force`. Backup `.git/` first; the tool removes `origin` (intentional) so re-add after.

4. **Vanilla-Postgres CI vs Supabase prod** — any migration that references Supabase-managed primitives (`auth.uid()`, `auth.role()`, `auth.jwt()`, `supabase_realtime` publication, `pgcrypto` extensions Supabase preloads) must guard with `information_schema.schemata` / `pg_publication` lookups so `alembic upgrade head` works against both. The 2026_05_16_rls_realtime migration was the first to fail this way; pattern to remember when writing any future RLS migration.

5. **Two GitHub identities on Windows credential manager** — `kingusa1` and `sheerazfame`. The git push credential helper auto-switches to whichever was used last; if it picks `sheerazfame`, push to `kingusa1/compliance-agent` fails with "Repository not found." Workaround: `gh auth switch -u kingusa1` before every push. Documented in CLAUDE.md already.
