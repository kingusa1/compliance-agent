# Wave 2 — Quickstart

Fast access to every observability layer added in Wave 2. For full runbook + production deploy notes, see [`observability.md`](./observability.md).

## Boot

```bash
# 1. Network (one-time)
docker network create compliance-net

# 2. Backend on host (port 8001)
cd backend && nohup ./venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8001 > /tmp/wave2-uvicorn.log 2>&1 &

# 3. Observability stack — set required secrets first
export GLITCHTIP_SECRET_KEY="$(python3 -c 'import secrets;print(secrets.token_hex(32))')"
export GLITCHTIP_PG_PASSWORD="$(python3 -c 'import secrets;print(secrets.token_hex(16))')"
export GRAFANA_ADMIN_PASSWORD="admin-dev-pass"
export GLITCHTIP_DOMAIN="http://localhost:8080"

docker compose -f docker-compose.observability.yml up -d
```

Wait ~30s for GlitchTip migrations.

## Access

| Layer | URL | Auth |
|---|---|---|
| Backend `/healthz` | http://localhost:8001/healthz | none |
| Backend `/readyz` | http://localhost:8001/readyz | none |
| Backend `/metrics` | http://localhost:8001/metrics | none |
| GlitchTip UI | http://localhost:8080 | register first user → superuser |
| Grafana | http://localhost:3001 | `admin` / `$GRAFANA_ADMIN_PASSWORD` |
| Prometheus | http://localhost:9090 | none |
| Loki API | http://localhost:3100 | none (push/query only, no UI) |
| Promtail | no UI; tails Docker → Loki | — |

## Smoke

```bash
# Custom pipeline + LLM metrics exposed
curl -s http://localhost:8001/metrics | grep -E "pipeline_step_duration|llm_calls_total" | head

# Loki search (after backend logs accumulate)
curl -s 'http://localhost:3100/loki/api/v1/query?query={compose_service=~"compliance.*"}' | jq

# Prom self + scrape targets
open http://localhost:9090/targets
```

## Grafana dashboards

Login → Dashboards → Compliance folder:

- **Pipeline** — per-step duration p50/p95/p99 + throughput
- **LLM** — call rate, escalation rate, latency by model
- **API** — RPS, latency p50/p95/p99, error rate per route
- **Errors** — ERROR-level log rate (Loki) + recent ERROR lines

## GlitchTip first-time setup

1. Open http://localhost:8080 → register first user (becomes superuser).
2. Create org `compliance`, project `compliance-backend` (copy DSN).
3. `export SENTRY_DSN=<dsn>` → restart uvicorn → trigger an unhandled exception → expect event in GlitchTip within 30 s.

## Caveats

- **Prometheus scrape target** is `compliance-backend:8001` (Docker DNS). Backend on host shows DOWN in Prometheus targets until backend is containerized on `compliance-net`. Workaround for local dev: change `infrastructure/prometheus/prometheus.yml` target to `host.docker.internal:8001`. Custom metrics still visible via direct `curl localhost:8001/metrics`.
- **Internal-only ports** (Loki, Prom, GlitchTip) bind `127.0.0.1` only. Grafana on `3001:3000` binds publicly — operator UI; expose via Cloudflare Tunnel in prod.
- **Required env vars** (`GLITCHTIP_SECRET_KEY`, `GLITCHTIP_PG_PASSWORD`, `GRAFANA_ADMIN_PASSWORD`) fail-fast at compose validate — see `.env.example`.

## Stop

```bash
pkill -f "uvicorn app.main:app"
cd /Users/gomaa/Documents/Compliance && docker compose -f docker-compose.observability.yml down
```

To wipe persistent data too:

```bash
docker compose -f docker-compose.observability.yml down -v
```

## Logs

- Backend: `tail -f /tmp/wave2-uvicorn.log`
- Any container: `docker logs -f compliance-grafana-1` (or any service)
- All obs containers: `docker compose -f docker-compose.observability.yml logs -f`
