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
