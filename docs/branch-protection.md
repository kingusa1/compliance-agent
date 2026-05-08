# Branch protection (Wave 5)

`main` is protected. Direct pushes are rejected; merges require a green PR with all required checks passing.

> **⚠ BLOCKED on free plan (2026-05-07).** GitHub Free disallows branch-protection rules on private repos. `apply-branch-protection.sh` currently returns `403 Upgrade to GitHub Pro or make this repository public`. Unblock paths: (a) upgrade `ArcadeTechLTD` org to **GitHub Team** ($4/user/month, recommended), (b) GitHub Pro on the owner account, or (c) make repo public (NOT advised — `audit_log` + customer-data refs in code). Until then, treat the rules below as **operator discipline**: every PR should have green CI before merge even though it isn't enforced.

## Required checks

| Check | Source workflow | Wave |
|---|---|---|
| `pytest` | `.github/workflows/test.yml` | Wave 1 |
| `vitest` | `.github/workflows/test.yml` | Wave 1 |
| `coverage` | `.github/workflows/coverage.yml` | Wave 1 |
| `touched-fns-gate` | `.github/workflows/touched-fns-gate.yml` | Wave 1 |

`playwright` is label-gated (`e2e` label triggers it) and is NOT a required check — it would block trivial PRs that don't need browser testing.

## Other rules

- **Linear history required** — disables merge commits. Squash-merge or rebase only.
- **Force-push disabled** on `main`.
- **PR review** from at least 1 reviewer required (CODEOWNERS optional, set up later).
- **Dismiss stale approvals** when new commits push — keeps reviews honest.
- **Conversation resolution required** before merge — keeps PR comments from being silently ignored.

## Apply / re-apply

Run `scripts/apply-branch-protection.sh` (uses `gh api` — requires `repo` scope). The script is idempotent: re-running with the same config produces no changes.

```bash
GH_TOKEN=<your-token> bash scripts/apply-branch-protection.sh
```

To verify current state:

```bash
gh api repos/ArcadeTechLTD/compliance-agent/branches/main/protection | jq
```

## Manual UI path (for first-time setup if CLI fails)

1. GitHub → repo → Settings → Branches → "Add rule".
2. Branch name pattern: `main`.
3. Tick:
   - Require a pull request before merging
     - Require approvals: 1
     - Dismiss stale pull request approvals when new commits are pushed
   - Require status checks to pass before merging
     - Require branches to be up to date before merging
     - Required status checks: `pytest`, `vitest`, `coverage`, `touched-fns-gate`
   - Require conversation resolution before merging
   - Require linear history
4. Untick "Allow force pushes" + "Allow deletions".
5. Save.

## Removing protection (DO NOT casually do this)

`gh api -X DELETE repos/ArcadeTechLTD/compliance-agent/branches/main/protection` removes all rules. Only run during a controlled break-glass procedure.
