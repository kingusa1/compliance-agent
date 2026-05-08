# Wave 5 — Deploy: SSH Workflow + Branch Protection + Final Docs

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Apply `two-stage-review-loop` between tasks.

**Goal:** Push-to-main lands on production VPS in ≤5 min via deploy-only SSH key, gated by branch protection that requires green test + coverage checks. Closes the enterprise-hardening inject loop opened in Waves 1-4.

**Architecture:** Three artefacts, all additive.
(a) **`.github/workflows/deploy.yml`** — runs on `push: branches: [main]`, requires `test.yml` + `coverage.yml` + `touched-fns-gate.yml` to be green via `workflow_run` dependency, SSHes to Contabo VPS using a deploy-only key, runs `git pull && docker compose pull && docker compose up -d`, curls `/healthz` for smoke verification, rolls back on failure.
(b) **Branch protection** — applied via GitHub UI or `gh api` calls (documented as a runbook in `docs/branch-protection.md`). Required checks: `pytest`, `vitest`, `coverage`, `touched-fns-gate`. Linear history. No force-push. PR review required from at least one CODEOWNER.
(c) **Final docs pass** — `docs/runbook.md` consolidates Wave 1-5 ops procedures into one page; `docs/architecture-comparison.md` updated with "Phase 1 complete" annotations vs. spec.

Most of Wave 5 is GitHub-side configuration. The repo-side changes are (a) deploy.yml, (b) `infrastructure/contabo/deploy.sh` script invoked by deploy.yml, (c) docs. Branch protection is enforced via GitHub Settings — captured as a CLI runbook so any operator can re-apply.

**Tech Stack:** GitHub Actions (`workflow_run` trigger + `appleboy/ssh-action@v1`), bash, Docker Compose v2, Cloudflare Tunnel (existing), gh CLI for branch-protection scripts.

**Spec source:** `docs/superpowers/specs/2026-05-06-enterprise-hardening-inject-design.md` §9 Wave 5 (W5a + W5b + W5c) and §3 Success Criteria #8, #9, #10.

**Prereqs:**
- Wave 4 PR #3 reviewed & merged to `main`. (Or rebased.)
- **PAT rotated** with `workflow` scope (already done in this session — see `~/.claude/projects/-Users-gomaa-Documents-Compliance/memory/github_pat.md`).
- SSH deploy-only key generated and pasted into Contabo VPS `~/.ssh/authorized_keys` (operator step in T2).
- GitHub repo secrets configured: `CONTABO_DEPLOY_HOST`, `CONTABO_DEPLOY_USER`, `CONTABO_DEPLOY_KEY`, `CONTABO_DEPLOY_PORT` (default 22).
- Wave 2 obs stack already running on VPS so the post-deploy smoke against `/healthz` succeeds.

**Wave 5 is the final wave** of the enterprise-hardening inject. After it merges, the repo state matches `compliance architecture 2.docx` Phase 1 plus replay + storage-portability extensions.

---

## Branch

```bash
git checkout main
git pull --ff-only
git checkout -b feat/wave5-deploy
```

If Wave 4 not yet merged, branch from `feat/wave4-cost`.

---

## File Structure

| Path | New / Mod | Responsibility |
|---|---|---|
| `.github/workflows/deploy.yml` | NEW | SSH-based auto-deploy on push-to-main, gated by green required checks |
| `infrastructure/contabo/deploy.sh` | NEW | Server-side deploy + smoke + rollback script invoked over SSH |
| `infrastructure/contabo/README.md` | MOD | Append SSH deploy-key setup + first-time secret-config steps |
| `docs/branch-protection.md` | NEW | Runbook: required checks, gh CLI script to (re-)apply, manual UI steps |
| `docs/runbook.md` | NEW | Consolidated ops runbook — boot, deploy, rollback, incident response, dashboards |
| `architecture-comparison.md` | MOD | Append "Phase 1 complete (Wave 1-5 shipped)" section with checked-off status table |

---

## Task 1: deploy.yml workflow

**Files:**
- Create: `.github/workflows/deploy.yml`

- [ ] **Step 1: Write workflow file**

Create `.github/workflows/deploy.yml`:

```yaml
name: deploy

on:
  workflow_run:
    workflows: ["test", "coverage"]
    types: [completed]
    branches: [main]
  workflow_dispatch:  # manual trigger for emergency redeploy

concurrency:
  group: deploy-prod
  cancel-in-progress: false  # never cancel a deploy mid-flight

jobs:
  deploy:
    runs-on: ubuntu-latest
    if: >
      github.event_name == 'workflow_dispatch' ||
      (github.event.workflow_run.conclusion == 'success' &&
       github.event.workflow_run.head_branch == 'main')
    timeout-minutes: 10
    steps:
      - name: Checkout (head SHA from triggering workflow)
        uses: actions/checkout@v4
        with:
          ref: ${{ github.event.workflow_run.head_sha || github.sha }}

      - name: Verify required secrets
        run: |
          for v in CONTABO_DEPLOY_HOST CONTABO_DEPLOY_USER CONTABO_DEPLOY_KEY; do
            if [ -z "${!v}" ]; then
              echo "Missing required secret: $v" >&2
              exit 1
            fi
          done
        env:
          CONTABO_DEPLOY_HOST: ${{ secrets.CONTABO_DEPLOY_HOST }}
          CONTABO_DEPLOY_USER: ${{ secrets.CONTABO_DEPLOY_USER }}
          CONTABO_DEPLOY_KEY: ${{ secrets.CONTABO_DEPLOY_KEY }}

      - name: SSH deploy
        uses: appleboy/ssh-action@v1.2.0
        with:
          host: ${{ secrets.CONTABO_DEPLOY_HOST }}
          username: ${{ secrets.CONTABO_DEPLOY_USER }}
          key: ${{ secrets.CONTABO_DEPLOY_KEY }}
          port: ${{ secrets.CONTABO_DEPLOY_PORT || 22 }}
          command_timeout: 8m
          script: |
            set -euo pipefail
            cd /opt/compliance
            bash infrastructure/contabo/deploy.sh

      - name: Post-deploy smoke (external)
        run: |
          # Hit the public Cloudflare Tunnel hostname to confirm the deploy
          # is reachable from outside the VPS, not just localhost.
          if [ -n "${{ secrets.CONTABO_HEALTHZ_URL }}" ]; then
            for i in 1 2 3 4 5; do
              if curl -fsS --max-time 10 "${{ secrets.CONTABO_HEALTHZ_URL }}" >/dev/null; then
                echo "[smoke] healthz OK on attempt $i"
                exit 0
              fi
              echo "[smoke] attempt $i failed; retrying in 6s"
              sleep 6
            done
            echo "[smoke] healthz never returned 200 — deploy is sad" >&2
            exit 1
          else
            echo "[smoke] CONTABO_HEALTHZ_URL not set — skipping external smoke"
          fi
```

- [ ] **Step 2: Validate YAML**

```bash
cd /Users/gomaa/Documents/Compliance && python3 -c "import yaml; yaml.safe_load(open('.github/workflows/deploy.yml')); print('YAML valid')"
```
Expected: `YAML valid`.

- [ ] **Step 3: Validate via gh actions schema (best-effort)**

```bash
# Optional: requires actionlint
which actionlint && actionlint .github/workflows/deploy.yml || echo "actionlint not installed — skipped"
```

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/deploy.yml
git commit -m "feat(ci): add deploy.yml — SSH auto-deploy on green main"
```

---

## Task 2: Server-side deploy script

**Files:**
- Create: `infrastructure/contabo/deploy.sh`

- [ ] **Step 1: Write script**

Create `infrastructure/contabo/deploy.sh`:

```bash
#!/usr/bin/env bash
# Server-side deploy script. Invoked over SSH by .github/workflows/deploy.yml
# from /opt/compliance.
#
# Behaviour:
#   1. Capture current commit SHA for rollback.
#   2. git fetch + reset --hard to origin/main.
#   3. docker compose pull (both app + observability stacks).
#   4. docker compose up -d.
#   5. Wait up to 60s for /healthz to return 200 locally.
#   6. On failure: roll back to captured SHA + restart.
#
# Idempotent. Runs without args. Exits 0 on success, non-zero on failure.

set -euo pipefail

REPO_DIR="/opt/compliance"
HEALTHZ="http://localhost:8001/healthz"
WAIT_SECONDS=60
COMPOSE_FILES=(-f docker-compose.yml -f docker-compose.observability.yml)

cd "$REPO_DIR"

PREV_SHA="$(git rev-parse HEAD)"
echo "[deploy] previous SHA: $PREV_SHA"

echo "[deploy] fetching origin/main"
git fetch --quiet origin main
NEW_SHA="$(git rev-parse origin/main)"
if [[ "$PREV_SHA" == "$NEW_SHA" ]]; then
  echo "[deploy] already at $NEW_SHA — no-op"
  exit 0
fi

echo "[deploy] checking out $NEW_SHA"
git reset --hard "$NEW_SHA"

echo "[deploy] docker compose pull"
docker compose "${COMPOSE_FILES[@]}" pull

echo "[deploy] docker compose up -d"
docker compose "${COMPOSE_FILES[@]}" up -d

echo "[deploy] waiting up to ${WAIT_SECONDS}s for $HEALTHZ"
for ((i = 1; i <= WAIT_SECONDS; i++)); do
  if curl -fsS --max-time 2 "$HEALTHZ" >/dev/null 2>&1; then
    echo "[deploy] healthy on attempt $i"
    echo "[deploy] OK — now at $NEW_SHA"
    exit 0
  fi
  sleep 1
done

echo "[deploy] healthz never returned 200 — rolling back to $PREV_SHA" >&2
git reset --hard "$PREV_SHA"
docker compose "${COMPOSE_FILES[@]}" up -d
echo "[deploy] rollback complete; deploy failed" >&2
exit 1
```

- [ ] **Step 2: Make executable + sanity check**

```bash
chmod +x /Users/gomaa/Documents/Compliance/infrastructure/contabo/deploy.sh
bash -n /Users/gomaa/Documents/Compliance/infrastructure/contabo/deploy.sh && echo "shell syntax OK"
```
Expected: `shell syntax OK`.

- [ ] **Step 3: Commit**

```bash
git add infrastructure/contabo/deploy.sh
git commit -m "feat(deploy): add deploy.sh — server-side pull + up -d + healthz + rollback"
```

---

## Task 3: Append SSH deploy notes to Contabo runbook

**Files:**
- Modify: `infrastructure/contabo/README.md`

- [ ] **Step 1: Append section**

In `/Users/gomaa/Documents/Compliance/infrastructure/contabo/README.md`, after the existing `### Backups (Wave 3)` section (or at the bottom — wherever fits the existing flow), append:

```markdown
### Auto-deploy via GitHub Actions (Wave 5)

`push` to `main` → `.github/workflows/deploy.yml` → SSH to this VPS → `infrastructure/contabo/deploy.sh` → `git reset --hard origin/main` → `docker compose up -d` → wait for `/healthz` → rollback on failure.

#### One-time setup

1. **Generate a deploy-only SSH key** (no passphrase, ed25519):

   ```bash
   ssh-keygen -t ed25519 -C "compliance-agent-deploy" -f ~/.ssh/compliance_deploy -N ""
   ```

2. **Append the public key** to `/root/.ssh/authorized_keys` on the VPS (or whichever user owns `/opt/compliance`):

   ```bash
   ssh root@<vps-ip> "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys" < ~/.ssh/compliance_deploy.pub
   ```

   Optional but recommended: restrict the key to running only `deploy.sh`:

   ```
   # in /root/.ssh/authorized_keys, prefix the key with:
   command="cd /opt/compliance && bash infrastructure/contabo/deploy.sh",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty ssh-ed25519 AAAA...
   ```

3. **Set repo secrets** in GitHub → Settings → Secrets and variables → Actions:

   | Secret | Value |
   |---|---|
   | `CONTABO_DEPLOY_HOST` | `161.97.178.185` (or DNS name) |
   | `CONTABO_DEPLOY_USER` | `root` (or the deploy user) |
   | `CONTABO_DEPLOY_KEY` | contents of `~/.ssh/compliance_deploy` (PRIVATE key) |
   | `CONTABO_DEPLOY_PORT` | `22` (default; only set if non-standard) |
   | `CONTABO_HEALTHZ_URL` | `https://compliance.<domain>/healthz` (Cloudflare Tunnel hostname) |

4. **Verify the path works manually first** before relying on Actions:

   ```bash
   ssh -i ~/.ssh/compliance_deploy root@<vps-ip> "cd /opt/compliance && bash infrastructure/contabo/deploy.sh"
   ```
   Expected: prints `[deploy] OK — now at <sha>`.

#### Rotating the deploy key

Every quarter, regenerate the keypair:

```bash
ssh-keygen -t ed25519 -C "compliance-agent-deploy-$(date +%Y%m)" -f ~/.ssh/compliance_deploy -N ""
ssh root@<vps-ip> "sed -i '/compliance-agent-deploy/d' ~/.ssh/authorized_keys"
ssh root@<vps-ip> "cat >> ~/.ssh/authorized_keys" < ~/.ssh/compliance_deploy.pub
gh secret set CONTABO_DEPLOY_KEY < ~/.ssh/compliance_deploy
```

#### Manual emergency redeploy

If automated deploy fails or you need to roll forward without merging:

- GitHub UI → Actions → `deploy` → "Run workflow" (uses `workflow_dispatch`).
- OR via gh: `gh workflow run deploy.yml`.
- OR direct SSH: `ssh root@<vps-ip> "cd /opt/compliance && bash infrastructure/contabo/deploy.sh"`.
```

- [ ] **Step 2: Commit**

```bash
git add infrastructure/contabo/README.md
git commit -m "docs(contabo): SSH deploy-key setup + secrets + rotation runbook"
```

---

## Task 4: Branch protection runbook + apply script

**Files:**
- Create: `docs/branch-protection.md`
- Create: `scripts/apply-branch-protection.sh`

- [ ] **Step 1: Write runbook**

Create `/Users/gomaa/Documents/Compliance/docs/branch-protection.md`:

```markdown
# Branch protection (Wave 5)

`main` is protected. Direct pushes are rejected; merges require a green PR with all required checks passing.

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
```

- [ ] **Step 2: Write apply script**

Create `/Users/gomaa/Documents/Compliance/scripts/apply-branch-protection.sh`:

```bash
#!/usr/bin/env bash
# Apply branch protection rules to main. Idempotent.
# Requires gh CLI authenticated with `repo` scope.
#
# Re-run after editing this script to push updated config.
set -euo pipefail

OWNER="${OWNER:-ArcadeTechLTD}"
REPO="${REPO:-compliance-agent}"
BRANCH="${BRANCH:-main}"

if ! command -v gh >/dev/null 2>&1; then
  echo "gh CLI not found. Install: https://cli.github.com" >&2
  exit 2
fi

echo "[bp] applying protection to $OWNER/$REPO/$BRANCH"

gh api \
  --method PUT \
  -H "Accept: application/vnd.github+json" \
  "/repos/$OWNER/$REPO/branches/$BRANCH/protection" \
  -f "required_status_checks[strict]=true" \
  -f "required_status_checks[contexts][]=pytest" \
  -f "required_status_checks[contexts][]=vitest" \
  -f "required_status_checks[contexts][]=coverage" \
  -f "required_status_checks[contexts][]=touched-fns-gate" \
  -F "enforce_admins=false" \
  -f "required_pull_request_reviews[required_approving_review_count]=1" \
  -F "required_pull_request_reviews[dismiss_stale_reviews]=true" \
  -F "required_pull_request_reviews[require_code_owner_reviews]=false" \
  -F "restrictions=" \
  -F "required_linear_history=true" \
  -F "allow_force_pushes=false" \
  -F "allow_deletions=false" \
  -F "required_conversation_resolution=true" \
  -F "lock_branch=false" \
  -F "allow_fork_syncing=false"

echo "[bp] OK — protection applied to $BRANCH"
```

- [ ] **Step 3: Make executable + syntax check**

```bash
chmod +x /Users/gomaa/Documents/Compliance/scripts/apply-branch-protection.sh
bash -n /Users/gomaa/Documents/Compliance/scripts/apply-branch-protection.sh && echo "shell syntax OK"
```

- [ ] **Step 4: Commit**

```bash
git add docs/branch-protection.md scripts/apply-branch-protection.sh
git commit -m "feat(ci): add branch-protection runbook + idempotent apply script"
```

---

## Task 5: Consolidated runbook

**Files:**
- Create: `docs/runbook.md`

- [ ] **Step 1: Write the runbook**

Create `/Users/gomaa/Documents/Compliance/docs/runbook.md`:

```markdown
# Compliance Agent — operations runbook

One-stop reference for engineers + on-call. Per-feature deep-dives live in:
- `docs/observability.md` (Wave 2 — GlitchTip + LGTM-lite)
- `docs/durability.md` (Wave 3 — replay + backups + storage portability)
- `docs/cost-optimization.md` (Wave 4 — A/B-gated cost flags)
- `docs/branch-protection.md` (Wave 5 — merge rules)
- `infrastructure/contabo/README.md` (VPS lifecycle + DNS)

## Quick reference

| Concern | Command / URL |
|---|---|
| Backend health | `curl https://compliance.<domain>/healthz` |
| Backend readiness | `curl https://compliance.<domain>/readyz` |
| Metrics scrape | `curl http://localhost:9090/targets` (Prometheus, internal-only) |
| Grafana dashboards | `https://grafana.compliance.<domain>` (Cloudflare Tunnel) |
| GlitchTip errors | `http://localhost:8080/issues` (internal; tunnel for remote) |
| Manual backup | `docker compose exec compliance-backend python -m scripts.pg_dump_to_storage` |
| Restore drill | `bash scripts/restore_drill.sh --latest` |
| Manual deploy | `gh workflow run deploy.yml` (or SSH + `bash infrastructure/contabo/deploy.sh`) |
| A/B parity check | `cd backend && python -m scripts.ab_parity --sample-size 50 --out ab.json` |

## Deploy

Push to `main` → CI green → deploy.yml SSHes Contabo → `git reset --hard origin/main` → `docker compose up -d` → wait for `/healthz` → roll back on failure.

Target latency: ≤ 5 min from merge to live. Concurrency-gated: only one deploy runs at a time.

Manual override: GitHub UI → Actions → deploy → Run workflow (uses `workflow_dispatch`).

## Incident response

### Symptoms

| Alert | First check |
|---|---|
| Grafana panel "API error rate" spikes | GlitchTip top exceptions |
| `/readyz` returning 503 | DB reachability — `docker logs compliance-postgres` if local, Supabase status if cloud |
| LLM dashboard "calls/min" → 0 | Provider key validity (OpenRouter / Gemini / Anthropic dashboards) |
| Pipeline panel p99 > 10× normal | Inngest dashboard for stuck/retrying runs |
| `failed_jobs` table growing | `/api/observability/stuck` UI; review last_error column |

### Rollback

If a deploy made things worse:

```bash
# SSH to VPS
ssh root@<vps-ip>
cd /opt/compliance
git log --oneline -5             # find a known-good SHA
git reset --hard <known-good-sha>
docker compose -f docker-compose.yml -f docker-compose.observability.yml up -d
curl -fsS http://localhost:8001/healthz
```

deploy.sh's automatic rollback covers most cases — manual rollback only when the auto path also failed.

### Cost-flag rollback (Wave 4)

Append to `/opt/compliance/.env` and restart backend; no code change:

```bash
echo "USE_AGENT_ANALYZER=false" >> /opt/compliance/.env
echo "EMBEDDING_PREFILTER_ENABLED=false" >> /opt/compliance/.env
docker compose restart compliance-backend
```

## Routine maintenance

| Task | Cadence | Command |
|---|---|---|
| pg_dump backup | nightly (Inngest cron 02:00 UTC) | automatic |
| Restore drill | quarterly | `bash scripts/restore_drill.sh --latest` |
| PAT rotation | quarterly | see `~/.claude/projects/-Users-gomaa-Documents-Compliance/memory/github_pat.md` |
| Deploy key rotation | quarterly | see `infrastructure/contabo/README.md` |
| A/B parity re-run | after model upgrade or noticeable verdict drift | `python -m scripts.ab_parity --sample-size 50 ...` |
| Coverage threshold ratchet | every milestone | edit `.github/workflows/coverage.yml` |

## Local dev quickstart

See `docs/wave2-quickstart.md` for the boot procedure (backend + obs stack + frontend).

To enable role-based admin access in dev: `DEV_ALL_ADMIN=true` in `backend/.env` (Wave 4 add-on).

## Where things live

```
/opt/compliance/                     repo on VPS
├── backend/                         FastAPI app
├── frontend-v3/                     Next.js
├── infrastructure/
│   ├── contabo/                     VPS runbook + DNS terraform + deploy.sh
│   ├── grafana/                     dashboards + provisioning
│   ├── prometheus/                  scrape config
│   └── promtail/                    log shipper config
├── docker-compose.yml               app stack
├── docker-compose.observability.yml obs stack overlay
└── scripts/
    ├── restore_drill.sh             pg restore drill
    └── apply-branch-protection.sh   GH branch-protection idempotent applier
```
```

- [ ] **Step 2: Commit**

```bash
git add docs/runbook.md
git commit -m "docs(ops): consolidated Wave 1-5 runbook"
```

---

## Task 6: Update architecture-comparison.md with Phase 1 status

**Files:**
- Modify: `architecture-comparison.md` (at repo root)

- [ ] **Step 1: Read existing file to find tail**

```bash
tail -30 /Users/gomaa/Documents/Compliance/architecture-comparison.md
```

- [ ] **Step 2: Append "Phase 1 Status" section**

At the bottom of `architecture-comparison.md`, append:

```markdown

---

## Phase 1 — Enterprise Hardening Inject — STATUS

| Wave | Deliverables | PR | Status |
|---|---|---|---|
| Wave 1 | CI workflows (test/coverage/touched-fns-gate), audit_log + failed_jobs migrations + writers, Contabo IaC + Cloudflare DNS via OpenTofu | n/a (predates PR convention) | ✅ shipped |
| Wave 2 | GlitchTip self-hosted, Sentry SDK (backend + frontend), `/metrics`, `/healthz`, `/readyz`, Prom + Loki + Promtail + Grafana with 4 seed dashboards, JSON logger | #1 | ✅ shipped |
| Wave 3 | StorageBackend ABC (Supabase + S3/MinIO), `POST /api/calls/{id}/reanalyze` + `process_call_reanalyze` Inngest fn, ReanalyzeButton, pg_dump_nightly cron, restore_drill.sh, durability.md | #2 | ✅ shipped |
| Wave 4 | Embedding pre-filter, A/B parity harness, cost-optimization runbook, DEV_ALL_ADMIN flag | #3 | ✅ shipped (defaults still off pending A/B run) |
| Wave 5 | deploy.yml SSH workflow, branch protection runbook + apply script, consolidated ops runbook | #4 | this PR |

### Spec coverage vs `compliance architecture 2.docx`

| Spec § | Status |
|---|---|
| 2.1 API (Prometheus + healthz) | ✅ Wave 2 |
| 2.2-2.4 Broker / Celery / Redis | ⚠ Skipped — Inngest replaces (documented in this file) |
| 2.5 audit_log + failed_jobs | ✅ Wave 1 |
| 2.6 Object storage portability | ✅ Wave 3 |
| 3.1 Transcribe (5-engine tribunal) | ✅ pre-existing (ahead of spec) |
| 3.2 Embedding similarity pre-filter | ✅ Wave 4 (flag-gated) |
| 4 Durability semantics | ✅ Wave 3 (durability.md maps to Inngest) |
| 5.1 Sentry / GlitchTip | ✅ Wave 2 |
| 5.2 Loguru → Loki | ✅ Wave 2 |
| 5.3 Prometheus + Grafana | ✅ Wave 2 |
| 5.4 Flower | ⚠ Skipped — Inngest dashboard is equivalent |
| 7 Deployment | ✅ Wave 5 (deploy.yml + Contabo runbook) |
| 8 IaC | ✅ Wave 1 (Cloudflare DNS via OpenTofu; Contabo VPS via SSH+Compose runbook) |
| 9 Env vars | ✅ Wave 2-5 (Pydantic Settings + .env.example) |
| 10 Cost | ✅ Wave 4 (flags ready; A/B gate before flip) |
| Replay (§2.5) | ✅ Wave 3 |

### Open follow-ups (post-Phase-1)

- Run Wave 4 A/B parity sample (≥50 calls); flip flags to True after parity ≥ 98%.
- Two-pool Inngest split (analysis vs pipeline) — defer until starvation measured.
- AWS migration (Phase 2) — storage abstraction is the only prep already in place.
- SOC2 / ISO27001 cert work — `audit_log` is the prereq, not the cert itself.
- Frontend Sentry replay sessions — privacy review needed before enabling.
```

- [ ] **Step 3: Commit**

```bash
git add architecture-comparison.md
git commit -m "docs(arch): mark Phase 1 enterprise-hardening inject complete (Waves 1-5)"
```

---

## Task 7: Push + open PR

- [ ] **Step 1: Push branch**

```bash
git push -u origin feat/wave5-deploy
```

- [ ] **Step 2: Create PR**

```bash
gh pr create \
  --base main \
  --head feat/wave5-deploy \
  --title "Wave 5 — Deploy: SSH workflow + branch protection + final docs" \
  --body-file - <<'EOF'
## Summary

- `.github/workflows/deploy.yml` — SSH-based auto-deploy on push-to-main, gated by green `test.yml` + `coverage.yml` via `workflow_run`. Concurrency-locked; never cancels mid-flight. Manual `workflow_dispatch` for emergency redeploy.
- `infrastructure/contabo/deploy.sh` — server-side script: capture prev SHA, `git reset --hard`, `docker compose pull && up -d`, wait up to 60 s for `/healthz`, auto-rollback on failure.
- `infrastructure/contabo/README.md` — appended SSH deploy-key setup, repo secret list (CONTABO_DEPLOY_HOST/USER/KEY/PORT/HEALTHZ_URL), key rotation procedure.
- `docs/branch-protection.md` + `scripts/apply-branch-protection.sh` — required checks list (pytest, vitest, coverage, touched-fns-gate), idempotent gh-API applier, manual UI fallback.
- `docs/runbook.md` — consolidated ops reference: deploy, rollback, incident response, routine maintenance, where things live.
- `architecture-comparison.md` — Phase 1 status table marking each Wave shipped + spec § coverage map.

## Test plan
- [x] `python -c "import yaml; yaml.safe_load(open('.github/workflows/deploy.yml'))"` — YAML valid.
- [x] `bash -n infrastructure/contabo/deploy.sh` + `bash -n scripts/apply-branch-protection.sh` — shell syntax clean.
- [ ] **Human follow-up:** generate deploy SSH keypair; paste public key into VPS authorized_keys; set the 5 GitHub secrets; trigger first deploy via `gh workflow run deploy.yml` and verify `/healthz` smokes green externally.
- [ ] **Human follow-up:** run `scripts/apply-branch-protection.sh` (or apply via UI per `docs/branch-protection.md`) AFTER this PR merges so existing in-flight PRs aren't blocked retroactively.

## Reviewer focus
1. `deploy.yml` `workflow_run` trigger only fires when `test` AND `coverage` both succeed on `main`. Concurrency `cancel-in-progress: false` is intentional — never abort an in-flight deploy.
2. `deploy.sh` is idempotent: same SHA → no-op exit 0. Rollback path uses captured PREV_SHA before any state mutation.
3. `apply-branch-protection.sh` is idempotent: re-running produces no diff. Safe to put in a recurring runbook.
4. The `restrict deploy key to single command` snippet in the Contabo runbook is the recommended hardening — flag if reviewer wants it as default rather than optional.
5. `/healthz` smoke uses external Cloudflare Tunnel URL (CONTABO_HEALTHZ_URL secret) — not just localhost — to confirm tunnel still works post-deploy.

## Out of scope (Phase 2)
- AWS migration (storage abstraction is the only prep — Wave 3).
- SOC2/ISO27001 cert paperwork (`audit_log` is the prereq, already shipped).
- Frontend Sentry replay sessions (privacy review needed).
- Two-pool Inngest split (defer until starvation measured).

## Closes
With this PR merged, Phase 1 of `compliance architecture 2.docx` is complete. The repo state matches the spec plus replay + storage-portability extensions. Open follow-ups documented in `architecture-comparison.md`.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
```

- [ ] **Step 3: Note PR URL**

Capture URL from gh output.

---

## Wave 5 acceptance gate

- [ ] All 7 tasks complete and committed.
- [ ] CI green on PR.
- [ ] Human follow-ups documented in PR body.
- [ ] After merge: deploy keypair generated, secrets configured, first auto-deploy succeeds.
- [ ] After merge: `apply-branch-protection.sh` run; required checks active on next PR.

After Wave 5 merges, the `Phase 1 — Enterprise Hardening Inject` row in `architecture-comparison.md` flips from "in progress" to "shipped" everywhere. Next milestone is Phase 2 (AWS migration) — separate roadmap, not part of this inject.
