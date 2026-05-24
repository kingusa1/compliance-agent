# Compliance Agent — Session Context

> **🛑 BINDING DOCTRINE — read before any tool call:**
> - [BRAIN/00_LAW_OF_ENTERPRISE_GRADE.md](BRAIN/00_LAW_OF_ENTERPRISE_GRADE.md) — every fix must meet the 12-line enterprise-grade checklist (schema, tests, observability, realtime, errors, idempotency, backwards-compat, UX, performance, security, audit, docs). A patch that misses any line is not done.
> - [BRAIN/00_LAW_OF_SKILLS.md](BRAIN/00_LAW_OF_SKILLS.md) — **v2 hard enforcement**: a skill is "fired" ONLY when the `Skill` or `Agent` tool was invoked by name in this session's transcript and the row is recorded in [BRAIN/06_Operations/Skill_Ledger.md](BRAIN/06_Operations/Skill_Ledger.md). Mentioning the skill in prose does NOT count. [BRAIN/06_Operations/Session_Self_Audit.md](BRAIN/06_Operations/Session_Self_Audit.md) runs before every "done" reply.
>
> **Then session context:**
> 1. [BRAIN/00_INDEX.md](BRAIN/00_INDEX.md) — vault map + "Read FIRST" pointer
> 2. [BRAIN/05_State/Live_State.md](BRAIN/05_State/Live_State.md) — current tip commit, deploy URLs, DB state
> 3. [BRAIN/06_Operations/Skill_Routing.md](BRAIN/06_Operations/Skill_Routing.md) — task→skill matrix with paste-ready tool calls
> 4. [BRAIN/06_Operations/Skill_Ledger.md](BRAIN/06_Operations/Skill_Ledger.md) — append-only invocation record
> 5. [BRAIN/06_Operations/Session_Self_Audit.md](BRAIN/06_Operations/Session_Self_Audit.md) — end-of-session validation
> 6. [BRAIN/06_Operations/Available_Skills.md](BRAIN/06_Operations/Available_Skills.md) — full ~2000-skill roster

## Skill execution contract (LAW v2 — non-negotiable)

The 2026-05-24 morning session shipped 3 commits and invoked **zero** verification skills. The session log claimed compliance because it *named* the skills in prose. That failure mode is closed:

1. **Trio declaration first.** The first three TodoWrite entries on every code task MUST be the Skill Trio Declaration:
   ```
   1. Skill trio declared · Primary: <name> · Parallel: <names | "none"> · Verification: <names>
   2. Invoke Primary <name> via Skill/Agent tool — proof in transcript
   3. Invoke Verification <name(s)> via Skill/Agent tool — proof in transcript
   ```
   No state-mutating tool (Edit/Write/Bash that changes things) runs until those are typed.

2. **Invocation = literal tool call.** A skill is fired ONLY by:
   - `Skill({ skill: "X" })`
   - `Agent({ subagent_type: "X", description: "...", prompt: "..." })`

   Prose like "I'll run `python-reviewer`" is NOT firing. The audit greps the transcript and catches the gap.

3. **Ledger every invocation.** Append one row to `BRAIN/06_Operations/Skill_Ledger.md` immediately after the tool returns. No backfilling.

4. **Audit before "done".** Run `BRAIN/06_Operations/Session_Self_Audit.md` and paste the verdict (`PASS` / `FAIL · re-running` / `WAIVED`) into the final user-facing reply. A `FAIL` verdict means the task is incomplete.

## Project identity

- **Owner:** Mohamed Hisham Ismail (`kingusa1`) for Watt Utilities
- **Live URLs:**
  - Frontend: https://compliance-agent-mu.vercel.app
  - Backend: https://compliance-agent-production-690e.up.railway.app
- **GitHub:** `kingusa1/compliance-agent` (must override the global `sheerazfame` identity when committing — use `-c user.name=kingusa1 -c user.email=IT@bbmgroup.io`)
- **Vercel:** project `prj_eHIyIFyxusNdCd6mR9Ff469NrcKO` on team `team_fNQJtpp1M2P2dkcoWvQIziCr`; auto-deploy NOT wired, trigger via API.

## Skill auto-routing (compact — v2 with literal tool calls)

Fire these without being asked when the task shape matches. Each row maps to a concrete tool call — naming the skill in prose is not firing. Full matrix in `BRAIN/06_Operations/Skill_Routing.md`.

| Task shape | Primary tool call | Verifier tool call |
|---|---|---|
| Plan-then-execute (multi-file) | `Agent({ subagent_type: "planner" })` | `Agent({ subagent_type: "code-reviewer" })` |
| Bug fix / regression | `Agent({ subagent_type: "debugger" })` | `Agent({ subagent_type: "code-reviewer" })` + unit test |
| Refactor / simplify | `Skill({ skill: "simplify" })` or `Agent({ subagent_type: "code-simplifier" })` | `Agent({ subagent_type: "code-reviewer" })` |
| New TS/React code | direct edit + `Skill({ skill: "react-best-practices" })` | `Agent({ subagent_type: "senior-frontend" })` + `tsc --noEmit` |
| New Python code | direct edit + `Skill({ skill: "python-patterns" })` | `Agent({ subagent_type: "python-reviewer" })` + AST smoke |
| New Go code | direct edit | `Agent({ subagent_type: "go-reviewer" })` + `go build` |
| New Kotlin code | direct edit | `Agent({ subagent_type: "kotlin-reviewer" })` |
| SQL / migration | direct edit + `Skill({ skill: "postgres-best-practices" })` | `Agent({ subagent_type: "database-reviewer" })` (BOTH pre- AND post-write) |
| Auth / secrets / SQL builder | direct edit | `Agent({ subagent_type: "security-reviewer" })` (BOTH pre- AND post-write) |
| Build broken | `Agent({ subagent_type: "build-error-resolver" })` | re-run failing build |
| Multi-perspective review | 3-4× `Agent({ ... })` **in a single tool-call block** | — |
| E2E flow | `Agent({ subagent_type: "e2e-runner" })` (Vercel Browser, Playwright fallback) | — |
| BRAIN-worthy outcome | direct Write to `BRAIN/` + update `00_INDEX.md` | — |

### Deterministic auto-trigger table (no decision required)

Match the trigger against `git diff --name-only` for this session. If the trigger fires and the corresponding tool call doesn't appear in the transcript, [Session_Self_Audit](BRAIN/06_Operations/Session_Self_Audit.md) fails the session.

| Files / pattern touched | Mandatory tool call |
|---|---|
| File imports `anthropic` / `@anthropic-ai/sdk` | `Skill({ skill: "claude-api" })` |
| Prompt-caching / thinking / tool-use feature work | `Skill({ skill: "prompt-caching" })` |
| Editing an Anthropic SDK app | `Agent({ subagent_type: "agent-sdk-dev:agent-sdk-verifier-py" })` (or `-ts`) |
| Any `backend/**/*.py` modified | `Agent({ subagent_type: "python-reviewer" })` after the change |
| Any `frontend-v3/src/**/*.{ts,tsx}` modified (not `tests/e2e/`) | `Agent({ subagent_type: "code-reviewer" })` (and/or `senior-frontend`) |
| `backend/alembic/versions/*.py` modified | `Agent({ subagent_type: "database-reviewer" })` |
| `Depends(current_*)` or `_require_admin` added/removed | `Agent({ subagent_type: "security-reviewer" })` |
| Tracker page touched | parallel `code-reviewer` + `python-reviewer` (single message) |
| Post-deploy walk-through | Playwright MCP `browser_navigate` + `browser_snapshot` per page |

### Parallel execution rule

When >1 skill is listed for a task, batch them in **one** tool-call block. Agents run in isolated context windows; serial chaining triples wall time for no benefit.

### Ledger and audit are mandatory

After every `Skill` / `Agent` tool call returns, append one row to `BRAIN/06_Operations/Skill_Ledger.md` §Active session with timestamp, role (primary/parallel/verification/auto-trigger), task-id, status, and evidence-ref (commit sha / file:line / agent summary).

Before any final "done" reply, run `BRAIN/06_Operations/Session_Self_Audit.md` and paste the verdict line. No "done" without a `PASS` (or explicit `WAIVED` with the user's verbatim quote).

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
