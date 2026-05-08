# Dev credentials — local observability stack

Verified working as of 2026-05-07. All values match the repo-root `.env` (gitignored). If any login fails, restart the obs stack: `docker compose -f docker-compose.observability.yml down && docker compose -f docker-compose.observability.yml up -d`.

## Local app login (cloud Supabase auth)

| Field | Value |
|---|---|
| Frontend | http://localhost:3000 |
| Email | `test@fame.dev` |
| Password | `test` |
| Role | `admin` (also forced by `DEV_ALL_ADMIN=true` in `backend/.env`) |

Other seeded accounts (same password unless rotated): `gomaa@fame.dev` (admin), `hitl-reviewer@test.local`, `audit-reviewer@test.local`, `test-user@test.local` (reviewers).

## Local-only quirks

- **Inngest emit warnings** in backend log (`INNGEST_EMIT_FAILED ...`) are expected when running locally — there is no Inngest dev server. Endpoints still write the audit row + return 202; only the cloud Inngest fanout never fires. To silence the warnings: `DISABLE_INNGEST_EMIT=1` in `backend/.env` (already set in synced env file). Production uses Inngest cloud; emit succeeds there.
- **`BACKEND_INTERNAL_URL`** must be set in `frontend-v3/.env.local` for Next.js dev rewrites to proxy to `http://localhost:8001`. Without it, `next.config.mjs` defaults to Docker DNS name `compliance-backend:8001` (which doesn't resolve outside the compose network).
- **Frontend dev server must run from this repo's path** (`/Users/gomaa/Documents/Compliance/frontend-v3`). The `init.sh` script kills lingering dev servers from the original `Compliance-Agent` repo automatically. Symptom of wrong-repo dev: missing Wave 3 features (e.g., Reanalyze button on call detail).

## Layer access

| Layer | URL | Username | Password | What it's for |
|---|---|---|---|---|
| **Backend** `/healthz` | http://localhost:8001/healthz | — | — | **Liveness probe.** "Is the FastAPI process up?" Returns `{"status":"ok"}`. Used by Docker healthcheck + Cloudflare Tunnel + load balancer pings. Does NOT touch the DB — fastest path. |
| **Backend** `/readyz` | http://localhost:8001/readyz | — | — | **Readiness probe.** "Can the process serve traffic right now?" Opens a DB connection + runs `SELECT 1`. Returns 200 with `{"status":"ready","checks":{"db":"ok"}}` or 503 with `{"status":"degraded","checks":{"db":"fail: ..."}}`. Used by deploy.sh to gate the rollback decision. |
| **Backend** `/metrics` | http://localhost:8001/metrics | — | — | **Prometheus exposition endpoint.** Custom metrics from `app/observability_metrics.py` (pipeline_step_duration_seconds, llm_calls_total, llm_call_duration_seconds) plus default `prometheus-fastapi-instrumentator` HTTP metrics. Scraped every 15 s by Prometheus. |
| **GlitchTip UI** | http://localhost:8080 | (register first user) | (you set on register) | **Sentry-API-compatible error tracking.** Catches uncaught exceptions from FastAPI + Next.js with stack traces, request payloads, and `job_id` / `call_id` tags. First user → superuser. **No preset admin account.** Log in with the email + password you chose at signup. |
| **Grafana** | http://localhost:3001 | `admin` | `admin-dev-pass` | **Dashboards UI.** Auto-provisioned datasources (Prometheus + Loki) and 4 dashboards in the `Compliance` folder: Pipeline (per-step duration), LLM (call rate + escalation), API (RPS + latency), Errors (Loki ERROR-rate). Login pulls from `GRAFANA_ADMIN_PASSWORD` in repo-root `.env`. |
| **Prometheus** | http://localhost:9090 | — | — | **Metrics scraper + TSDB.** Scrapes `compliance-backend:8001/metrics` every 15 s, retains 14 d. `/targets` shows scrape state (up/down). `/graph` lets you ad-hoc query PromQL. No auth on dev. |
| **Loki API** | http://localhost:3100 | — | — | **Log aggregator.** Receives JSON logs from Promtail. No UI — query via Grafana → Explore → Loki, e.g. `{compose_service="compliance-backend"}` or filter by `job_id="<id>"`. Direct API: `curl 'http://localhost:3100/loki/api/v1/query?query={compose_service=~"compliance.*"}'`. |
| **Promtail** | (no UI) | — | — | **Log shipper.** Sidecar that tails Docker container stdout, parses the JSON line (extracting `job_id` / `step` / `level` as labels), and pushes to Loki. Status: `docker logs compliance-promtail-1`. Reads `/var/run/docker.sock` read-only. |

## Where each dashboard helps

| Dashboard | When you open it |
|---|---|
| **Pipeline** (Grafana → Compliance → Pipeline) | Investigating slow calls; checking which step (`download_audio`, `transcribe`, `analyze_checkpoints`, `score`, `finalize`) is the bottleneck on a given window. |
| **LLM** (Grafana → Compliance → LLM) | Watching cost: escalation rate, calls/min, p50/p95 latency by model. After Wave 4 flag flip, escalation rate should drop ≪ baseline. |
| **API** (Grafana → Compliance → API) | API health: RPS by route, p50/p95/p99 latency, 4xx/5xx error rate per route (excludes `/metrics`, `/healthz`, `/readyz`). |
| **Errors** (Grafana → Compliance → Errors) | ERROR-level log rate over time + tail of recent ERROR lines (Loki query). For full stack traces with request context, follow the link to GlitchTip. |
| **GlitchTip → Issues** (http://localhost:8080/issues) | Per-exception view: stack trace, breadcrumbs, request payload, `job_id` tag. First place to look on an alert from the Errors dashboard. |
| **Prometheus → /targets** (http://localhost:9090/targets) | Scrape health. If a custom metric is missing, this tells you whether Prom can reach the backend at all. |

## GlitchTip first-time registration

GlitchTip ships with **no preset users**. Open http://localhost:8080, click "Sign up", and fill in:

1. Email + a password you'll remember (let Chrome save it to Keychain).
2. First user registered = superuser automatically.
3. Create org `compliance` + project `compliance-backend` → copy the DSN.
4. Paste DSN into `backend/.env`:
   ```bash
   echo "SENTRY_DSN=<dsn-from-glitchtip>" >> backend/.env
   ```
5. Restart backend:
   ```bash
   pkill -f "uvicorn app.main:app"
   cd backend && nohup ./venv/bin/uvicorn app.main:app --port 8001 > /tmp/uvi.log 2>&1 &
   ```

Trigger a deliberate exception to verify capture (e.g. hit a route with malformed payload).

## Postgres (GlitchTip's own DB — internal)

Not exposed to host. Inside compose network only:

| Field | Value | Purpose |
|---|---|---|
| host | `glitchtip-postgres` | Internal DNS name (resolves on `compliance-net`) |
| port | `5432` | Postgres default |
| db | `glitchtip` | DB name |
| user | `glitchtip` | DB role |
| password | `8d34012314c0930dcad01b75b08a9df0` | From repo-root `.env` (`GLITCHTIP_PG_PASSWORD`). **Locked into the volume** at first boot — changing the env var does NOT rotate the actual DB password without a `down -v` wipe. |

Direct query (rarely needed):
```bash
docker exec compliance-glitchtip-postgres-1 psql -U glitchtip glitchtip -c "SELECT count(*) FROM users_user;"
```

## Resetting Grafana admin password

If `admin-dev-pass` stops working:

```bash
docker exec compliance-grafana-1 grafana-cli admin reset-admin-password '<new>'
```

Then update repo-root `.env` so it survives the next `docker compose down/up`.

## Resetting GlitchTip superuser

Locked out? Create a new superuser via Django shell:

```bash
docker exec -it compliance-glitchtip-web-1 ./manage.py createsuperuser
```

## Production note

NONE of these credentials apply on the Contabo VPS. Production secrets live in `/opt/compliance/.env` and are managed via Cloudflare Tunnel + GitHub Actions Secrets (Wave 5). See `infrastructure/contabo/README.md` for the deploy-time secrets table.
