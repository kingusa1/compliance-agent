# Compliance Agent — Session Context

> **What to read first when resuming a session.**
> 1. [BRAIN/00_INDEX.md](BRAIN/00_INDEX.md) — vault map + "Read FIRST" pointer
> 2. [BRAIN/05_State/Live_State.md](BRAIN/05_State/Live_State.md) — current tip commit, deploy URLs, DB state
> 3. [BRAIN/06_Operations/Skill_Routing.md](BRAIN/06_Operations/Skill_Routing.md) — task→skill matrix (this file's source of truth)
> 4. [BRAIN/06_Operations/Available_Skills.md](BRAIN/06_Operations/Available_Skills.md) — full ~1500-skill roster

## Project identity

- **Owner:** Mohamed Hisham Ismail (`kingusa1`) for Watt Utilities
- **Live URLs:**
  - Frontend: https://compliance-agent-mu.vercel.app
  - Backend: https://compliance-agent-production-690e.up.railway.app
- **GitHub:** `kingusa1/compliance-agent` (must override the global `sheerazfame` identity when committing — use `-c user.name=kingusa1 -c user.email=IT@bbmgroup.io`)
- **Vercel:** project `prj_eHIyIFyxusNdCd6mR9Ff469NrcKO` on team `team_fNQJtpp1M2P2dkcoWvQIziCr`; auto-deploy NOT wired, trigger via API.

## Skill auto-routing (compact)

Fire these without being asked when the task shape matches. Full matrix in `BRAIN/06_Operations/Skill_Routing.md`.

| Task shape | Fire | Verify with |
|---|---|---|
| Plan-then-execute (multi-file) | `planner` Agent | `code-reviewer` post-edit |
| Bug fix / regression | `debugger` Agent | `code-reviewer` + unit test |
| Refactor / simplify | `simplify` skill or `code-simplifier` Agent | `code-reviewer` |
| New TS/React code | direct edit | `senior-frontend` + `tsc --noEmit` |
| New Python code | direct edit | `python-reviewer` + AST smoke |
| New Go code | direct edit | `go-reviewer` + `go build` |
| New Kotlin code | direct edit | `kotlin-reviewer` |
| SQL / migration | direct edit | `database-reviewer` (BOTH pre- and post-write) |
| Auth / secrets / SQL builder | direct edit | `security-reviewer` (BOTH pre- and post-write) |
| Build broken | `build-error-resolver` Agent | re-run failing build |
| Pre-push gate | `comprehensive-review-full-review` | — |
| Multi-perspective review | 3-4× Agent **in a single tool-call block** | — |
| E2E flow | `e2e-runner` Agent (Vercel Browser, Playwright fallback) | — |
| BRAIN-worthy outcome | direct write to `BRAIN/`, always update `00_INDEX.md` | — |

### Auto-trigger on code patterns (no slash needed)

- File imports `anthropic` / `@anthropic-ai/sdk` → `claude-api` skill
- Prompt-caching / thinking / tool-use feature work → `prompt-caching` skill
- Editing an Anthropic SDK app → `agent-sdk-dev:agent-sdk-verifier-*` after the change

### Parallel execution rule

When >1 skill is listed for a task, batch them in **one** tool-call block. Agents
run in isolated context windows; serial chaining triples wall time for no benefit.

## Domain-specific rules (this project)

- **Rubric router** (`backend/app/agents/rubric_router.py`) — 4-step LOA fallback (phase tag → name~'LOA' → both → V1). Don't tighten the filter; production seed data has `lifecycle_phase=NULL` for every supplier script.
- **Agent-name extraction** (`backend/app/analysis.py`) — two-layer: regex pre-pass (deterministic, fires for unusual names like "Afak") + LLM (handles surname + customer). Regex wins ONLY when LLM returns "Unknown". 9/9 smoke-test cases must pass before touching either layer.
- **Tracker page** (`frontend-v3/src/app/(admin)/tracker/`) — touching any file here ALWAYS triggers parallel `code-reviewer` + `python-reviewer` post-edit. Tracker has gone through 9 wiring fixes in one session; regressions hide easily.
- **Live UI walk-through** post-deploy — use Playwright MCP to hit `/queue`, `/calls/[id]`, `/tracker`, `/customers`, `/rejections`, `/agents`, `/dashboard`. Validate before reporting "done".
- **No dubai.news modifications** anywhere in this repo's flow — separate codebase, different rules.

## Doing risky actions

- **Never** push `BRAIN/` edits inside a feature commit — keep them as separate `docs(brain): …` commits.
- **Never** skip `tsc --noEmit` / `python -c "import ..."` after backend or type-system changes.
- **Confirm before** destructive ops (`git reset --hard`, force-push, dropping tables, `--no-verify`).
- **Default git identity is wrong** for this repo — see "GitHub" above. Earlier sessions pushed under the right identity; the credential helper can revert mid-session, leading to "Repository not found" errors. If push fails with that error, that's the cause.

## field_sources value vocabulary invariant

Before adding any new string to `Rejection.field_sources` or `CustomerDeal.field_sources` server-side, also add it to:

- `frontend-v3/src/lib/queries/tracker.ts` → `TrackerFieldSource` union
- `frontend-v3/src/app/(admin)/tracker/SourceBadge.tsx` → `STYLES` map

Otherwise `SourceBadge` crashes with `Cannot read properties of undefined (reading 'bg')` and the **entire /tracker page** errors out with "This page couldn't load". `SourceBadge` now has a defensive `if (!s) return null;` guard, but defence-in-depth: the union is the contract — keep both sides in sync.

Grep before pushing any backend change that stamps a new source tag:
```bash
grep -rn "TrackerFieldSource\b" frontend-v3/src
```

## CI parity guardrail — run touched tests BEFORE every push

**MUST run touched + impacted tests locally before `git push`.** The GitHub Actions `coverage` workflow runs the full `pytest` suite — and it FAILS on stale tests just as fast as on new bugs. Recent regressions that slipped through:

- `test_ai_rejection_reason::test_ai_rejection_reason_propagates_to_rejection_row` asserted on `Rejection.outcome_narrative` after the 2026-05-15 audit moved the AI narrative write to `Rejection.fix_narrative`. CI red for 5 commits before being caught.
- `test_routes.py::test_retry_call_*` (4 tests) returned 401 after `Depends(current_reviewer)` was added to `/api/calls/{id}/retry`. CI red for 5 commits before adding `app.dependency_overrides[current_reviewer]` to the test module's setup.

### The minimum gate before every push

```bash
# Touched-file tests (must pass)
./venv/Scripts/python.exe -m pytest tests/test_<area>.py -q --tb=line

# Once a session ends OR before merging to main, run the equivalent of CI's coverage job:
./venv/Scripts/python.exe -m pytest -q --tb=line
```

### When changing **any** of these patterns, also re-run the named tests:

| Change | Re-run |
|---|---|
| Add/remove `Depends(current_reviewer)` or `Depends(_require_admin)` on a route | `tests/test_routes.py` + grep for that route's existing test file |
| Move which Rejection column an AI field writes to | `tests/test_ai_rejection_reason.py` + `tests/test_rejection_factory*.py` |
| Add/remove fields from `TrackerRow` or `tracker_aggregator._*_row` | `tests/test_tracker_aggregator.py` |
| Alembic migration | `alembic upgrade head` on a fresh SQLite (the CI workflow does this) |
| New endpoint that writes `ReviewerEdit` audit rows | confirm `reviewer_edits` schema allows the (rejection_id, call_id) combo |

### If CI breaks despite the gate

1. `gh run list --limit 5 --workflow=coverage` — pick the failed run id
2. `gh run view <id> --log-failed | tail -80` — look for `FAILED tests/...` lines
3. Fix locally → re-run the specific failing test → commit + push
4. **Never push more commits on top of a red CI** — each additional commit costs an extra full re-run (~7 min) and clouds the failure diff.

## When work is local-only

User regularly says "don't push, fix locally". Track local fixes in TodoWrite,
typecheck before claiming done, and surface a clear list of unpushed files
when reporting back.

## Self-improvement loop

After every multi-skill task, append findings to `BRAIN/06_Operations/Skill_Routing.md`
under "Anti-patterns" so the next session's routing improves. Reviewer hallucinations
specifically belong there — today's `code-reviewer` invented a broken test
assertion that didn't exist.
