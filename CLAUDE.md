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

## When work is local-only

User regularly says "don't push, fix locally". Track local fixes in TodoWrite,
typecheck before claiming done, and surface a clear list of unpushed files
when reporting back.

## Self-improvement loop

After every multi-skill task, append findings to `BRAIN/06_Operations/Skill_Routing.md`
under "Anti-patterns" so the next session's routing improves. Reviewer hallucinations
specifically belong there — today's `code-reviewer` invented a broken test
assertion that didn't exist.
