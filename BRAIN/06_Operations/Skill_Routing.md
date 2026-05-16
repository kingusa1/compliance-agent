---
created: 2026-05-14
tags: [operations, skills, routing, auto-invocation]
---

# Skill Routing — task pattern → which skill(s) to fire

> Companion to [[Available_Skills]] (the full ~1500-skill roster). This
> file is the **routing matrix**: given a task shape, what does the
> assistant invoke, in what order, and what does it pass downstream.
>
> Loaded into context via the project-root `CLAUDE.md` so the right
> skills auto-fire without the user having to name them.

---

## How to read this file

| Column | Meaning |
|---|---|
| **Task pattern** | User intent expressed as a verb-phrase or a code smell. |
| **Primary skill** | The single skill that owns the task end-to-end. |
| **Parallel reviewers** | Skills launched alongside the primary for cross-perspective coverage. Run with `Agent` tool in a SINGLE tool-call block. |
| **Verification** | What the assistant fires **after** the primary completes to catch regressions. |

A blank "Parallel reviewers" means the primary is sufficient. A blank
"Verification" means the post-fix typecheck / smoke test is enough.

---

## Routing matrix

### 1 · Planning + scoping

| Task pattern | Primary | Parallel reviewers | Verification |
|---|---|---|---|
| "Plan first / write a plan" / non-trivial feature | `plan` (or `Plan` Agent) | — | — |
| "Brainstorm" / "what could we do about X" | `brainstorm` | — | — |
| "Help me decide" / weighing 2-3 options | `multi-advisor` or 2× `senior-architect` in parallel | — | — |
| Strategic / architecture decision | `architect` Agent | `senior-architect` skill | — |
| Roadmap / phase plan | `gsd plan-phase` workflow | — | — |
| Pre-planning consultant on ambiguous specs | `analyst` Agent | — | — |

### 2 · Implementation

| Task pattern | Primary | Parallel reviewers | Verification |
|---|---|---|---|
| New feature, single-file change | direct edit | — | `code-reviewer` post-edit |
| New feature, multi-file change | direct edits w/ TodoWrite plan | — | `code-reviewer` + relevant `senior-*` |
| Bug fix | `gsd-debugger` or `debugger` Agent | — | `code-reviewer` + targeted unit test |
| Refactor / cleanup | `simplify` or `code-simplifier` Agent | — | `code-reviewer` |
| Dead-code / consolidation | `refactor-cleaner` Agent | — | typecheck + test suite |
| TypeScript / React / Next.js implementation | direct edits | `senior-frontend` | `tsc --noEmit` |
| Python / FastAPI implementation | direct edits | `python-reviewer` | AST + import smoke |
| Go implementation | direct edits | `go-reviewer` | `go build` + `go vet` |
| Kotlin / Android implementation | direct edits | `kotlin-reviewer` | gradle build |
| Database migration / schema change | direct edits | `database-reviewer` | `database-reviewer` again post-write |
| Security-sensitive change (auth, secrets, SQL builder) | direct edits | `security-reviewer` | `security-reviewer` post-write |
| Test writing | `tdd-guide` Agent (red → green → refactor) | — | run the actual tests |

### 3 · Build / deploy

| Task pattern | Primary | Verification |
|---|---|---|
| Build broken / type errors | `build-error-resolver` Agent | re-run failing build |
| Go build broken | `go-build-resolver` Agent | `go build` |
| Kotlin build broken | `kotlin-build-resolver` Agent | gradle build |
| Deploy via Vercel API | direct curl POST to `/v13/deployments` | poll `readyState` |
| Deploy via Railway | git push to `main` (auto-deploy) | poll `/api/stats` |
| `/ship` slash command | `gsd ship` workflow | smoke test the live URL |

### 4 · Code review (post-implementation)

| Task pattern | Primary | Parallel reviewers |
|---|---|---|
| Reviewing a single PR / commit | `code-reviewer` Agent | — |
| Multi-perspective deep review | 3-4× Agent parallel (`code-reviewer`, `senior-architect`, `security-reviewer`, `database-reviewer` as appropriate) | — |
| Pre-push gate | `comprehensive-review-full-review` | — |
| `/ultrareview` cloud review | user-triggered only (cannot invoke from assistant) | — |

### 5 · Debugging

| Task pattern | Primary | Verification |
|---|---|---|
| Bug with a stack trace | `debugger` Agent | post-fix unit test |
| Stuck / unclear failure mode | `tracer` Agent (evidence-driven causal tracing) | post-fix smoke |
| Flaky / hang / timing-dependent bug | `systematic-debugging` skill | re-run the failing scenario |
| Production incident (DB / runtime) | `gsd-debugger` Agent | post-mortem write-up |
| Bug-bounty / forensic exploit hunt | `bug-hunter` + `find-bugs` in parallel | — |

### 6 · Verification

| Task pattern | Primary |
|---|---|
| "Is this actually done?" | `verifier` Agent (goal-backward analysis) |
| "Did the fixes hold?" (post-fix sweep) | `code-reviewer` Agent again on the diff |
| E2E user journey | `e2e-runner` Agent (Vercel Agent Browser preferred, Playwright fallback) |
| Live-UI smoke check | Playwright MCP (`browser_navigate`, `browser_evaluate`, `browser_snapshot`) |

### 7 · Documentation + BRAIN

| Task pattern | Primary | Notes |
|---|---|---|
| Update READMEs / API docs | `doc-updater` Agent (runs `/update-codemaps` + `/update-docs`) | — |
| Codemap regeneration | `update-codemaps` skill | — |
| Add new project knowledge | direct write to `BRAIN/` | always update `BRAIN/00_INDEX.md` |
| End-of-session log | direct write to `BRAIN/04_Sessions/YYYY-MM-DD_Session_<topic>.md` | also bump `Live_State.md` tip commit |
| Technical writing (clean prose) | `writer` Agent (Haiku — cheap + fast) | — |

### 8 · Domain-specific (this project)

| Task pattern | Primary | Notes |
|---|---|---|
| Touch the rubric / classifier | direct edits | always `python-reviewer` after; LOA router has 4-step fallback (BRAIN §rubric_router) |
| Touch the call-detail page | direct edits | run `tsc --noEmit` + Playwright snapshot the page after |
| Touch the tracker | direct edits | always run BOTH `code-reviewer` (frontend) + `python-reviewer` (backend) in parallel post-edit |
| Anything affecting `agent_name` / `customer_name` extraction | direct edits to `analysis.py` | smoke-test `_extract_agent_name_regex` against 9 ground-truth cases |
| Live UI walk-through after deploy | Playwright MCP | navigate `/queue`, `/calls/[id]`, `/tracker`, `/customers`, `/rejections`, `/agents`, `/dashboard` |
| Validate live LOA grading | upload `Evangelical church LOA.mp3` via `/api/calls/upload`, then GET `/api/calls/{id}/segments` | expect `rubric_kind=supplier_script_loa` |
| Validate live agent-name extraction | POST `/api/admin/backfill-agent-names?apply=false` | expect candidates list, none if all populated |

### 9 · OAuth integrations

These require user authentication and CAN'T auto-fire — first call returns
an authentication URL:

`Asana`, `Atlassian`, `Box`, `Canva`, `Gmail`, `Google_Drive`, `HubSpot`,
`Intercom`, `Linear`, `Microsoft_365`, `Notion`, `Windsor_ai`, `monday_com`,
`higgsfield`, `WordPress_com`. Use `claude_ai_<service>__authenticate` to
start the flow; `complete_authentication` returns the access token.

### 10 · Claude API / Anthropic SDK work

**Pattern-based auto-trigger** — fires when ANY of these are true:
- Editing a file that imports `anthropic` or `@anthropic-ai/sdk`
- Editing prompt caching / thinking / batch / tool use / citations code
- Migrating Claude model versions (4.5 → 4.6 → 4.7)
- User asks about prompt cache hit rate

Primary: `claude-api` skill. Parallel: `prompt-caching`, `prompt-engineer`.

### 11 · Anthropic SDK app authoring

Detected by file path or import. Fires:
- `agent-sdk-dev:new-sdk-app` — creating a new SDK app
- `agent-sdk-dev:agent-sdk-verifier-py` — after editing a Python SDK app
- `agent-sdk-dev:agent-sdk-verifier-ts` — after editing a TS SDK app

---

## Parallel-execution rules

When multiple skills are listed, **batch them in a single tool-call block**:

```text
[single message containing N Agent tool uses]
```

Each Agent runs in its own context window — they don't pollute each other,
and their outputs come back in parallel. Use this for:
- Multi-perspective review (3 reviewers)
- Cross-language audit (frontend + backend reviewers)
- Security + correctness + performance triad

**Do NOT** chain them serially when they don't depend on each other —
that triples wall time for no benefit.

---

## Anti-patterns

- **Don't** invoke a skill that isn't in the system-reminder allowlist —
  the `Skill` tool refuses unknown names.
- **Don't** skip verification on a "trivial" fix — that's how regressions
  enter the codebase (audit caught 3 today: double banner, stale textarea,
  hallucinated test failure).
- **Don't** call `tdd-guide` on a refactor with no observable behaviour
  change — fall back to `simplify` or direct edit + post-edit review.
- **Don't** treat reviewer output as gospel — verify findings against
  the actual file (today's `code-reviewer` hallucinated a broken assertion
  that didn't exist).
- **Don't** push BRAIN edits inside a feature commit — keep them as
  separate `docs(brain): …` commits so a `git revert` on the feature
  doesn't erase the session log.

- **Don't** change a production behaviour without grepping the test
  suite for the OLD assertion. This pattern has now bitten us **four**
  times — CI was red for between 5 and 6 commits each time:

  | Date | Production change | Stale test asserting OLD behaviour |
  |---|---|---|
  | 2026-05-15 | AI narrative write moved from `Rejection.outcome_narrative` → `Rejection.fix_narrative` | `test_ai_rejection_reason::test_ai_rejection_reason_propagates_to_rejection_row` |
  | 2026-05-15 | `Depends(current_reviewer)` added to `/api/calls/{id}/retry` | `test_routes.py::test_retry_call_*` (×4) returning 401 |
  | 2026-05-15 | `a83e441` added medium-only pass-rate gate (medium-only at <50% → `review` instead of `coaching`) | `test_checkpoint_analyzer::test_all_checkpoints_mixed_results` asserting bucket=`coaching` |
  | 2026-05-16 | `e1c8d3b` flipped vulnerability `risk_tag` from `"Vulnerable"` → `None` to satisfy `ck_flags_risk_tag` CHECK | `test_vulnerability.py::test_detect_emits_medium_when_only_stage1_fires`, `test_detect_emits_high_when_both_stages_agree` |

  **The pre-push gate**: before any commit that mutates one of these,
  grep for the OLD assertion string and update both at once.

  ```bash
  # Before changing a CHECK-constrained enum value or a bucket-gate threshold
  grep -rn "risk_tag.*Vulnerable\|bucket.*coaching\|outcome_narrative" backend/tests/
  ```

  Patterns that warrant the grep:
  - **CHECK constraint changes** — anything where the DB rejects values
    outside an enum (risk_tag, stage, call_type, status, lifecycle_phase).
    Always look at `backend/alembic/versions/*ck_*.py` first.
  - **Severity-bucket gates** — `analysis.py` / `checkpoint_analyzer.py`
    bucket selection. Any change to the critical/high/medium thresholds
    or the pass-rate escalation guard.
  - **Auth dependency adds** — adding `Depends(current_reviewer)` or
    `Depends(_require_admin)` to any route flips that route's tests
    from anonymous-200 to 401. The fix is a single line in
    `app.dependency_overrides`.
  - **Column-write moves** — moving a field from `Rejection.X` to
    `Rejection.Y` requires updating every test that asserted on `X`.

  Goal: keep CI green so push-after-push doesn't burn ~7 min of runner
  time per stale-test discovery.

---

## Self-improvement loop

After every multi-skill task, ask:
1. Did the primary skill catch what mattered? If no, **add to BRAIN/05_State/Known_Issues** so next session's primary picks up the gap.
2. Did the parallel reviewers find anything the primary missed? If yes, that's the strongest case for keeping them in this routing matrix — note the finding.
3. Did a reviewer hallucinate? Log it here under "Anti-patterns" so the pattern is recognised next time.

This file is the **closed-loop** between observed behaviour and next-session
behaviour. Update it whenever the routing surprises you.
