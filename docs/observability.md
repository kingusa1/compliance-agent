# Observability runbook (Wave 2)

## Stack

| Service | Image | Port | Purpose |
|---|---|---|---|
| `glitchtip-web` | `glitchtip/glitchtip:v4.1` | 8080 | Sentry-API error tracking UI |
| `glitchtip-worker` | same | — | Celery worker + beat |
| `glitchtip-postgres` | `postgres:16-alpine` | — | GlitchTip DB |
| `glitchtip-redis` | `redis:7-alpine` | — | GlitchTip broker |
| `prometheus` | `prom/prometheus:v2.55.1` | 9090 | Metric scraper + TSDB |
| `loki` | `grafana/loki:3.2.1` | 3100 | Log aggregator |
| `promtail` | `grafana/promtail:3.2.1` | — | Docker stdout shipper |
| `grafana` | `grafana/grafana:11.3.0` | 3001 | Dashboards |

## Bring up locally

```bash
# 1. Pre-req: app stack network exists
docker network create compliance-net 2>/dev/null || true

# 2. Populate observability secrets in .env (see .env.example)
#    GLITCHTIP_SECRET_KEY, GLITCHTIP_PG_PASSWORD, GRAFANA_ADMIN_PASSWORD

# 3. Boot
docker compose -f docker-compose.observability.yml up -d

# 4. Open
#    http://localhost:8080  GlitchTip (sign up admin user, create project, copy DSN)
#    http://localhost:3001  Grafana   (admin / GRAFANA_ADMIN_PASSWORD)
#    http://localhost:9090  Prometheus
```

## First-time GlitchTip setup

1. Open `http://localhost:8080`, register the first user (becomes superuser).
2. Create org `compliance` and projects `compliance-backend` and `compliance-frontend`.
3. Copy each project's DSN.
4. Backend: `SENTRY_DSN=<backend DSN>` in `backend/.env`, restart uvicorn.
5. Frontend: `NEXT_PUBLIC_SENTRY_DSN=<frontend DSN>` in `frontend-v3/.env.local`, restart `next dev`.

## Smoke procedure

### Backend error → GlitchTip

```bash
# Trigger an unhandled exception via temporary debug route
curl -X POST http://localhost:8001/__debug_raise   # only present if you add a stub; else use any route that raises
```
Open GlitchTip, expect the exception within 30 s with stack trace + request URL.

### Metrics → Prometheus → Grafana

```bash
curl -s http://localhost:8001/metrics | grep -E "^(http_requests_total|pipeline_step_duration_seconds_count|llm_calls_total)" | head -5
```
Open Grafana → Explore → Prometheus → query `up{job="compliance-backend"}` → expect `1`.

### Logs → Loki → Grafana

```bash
docker logs compliance-backend --tail 5
```
Open Grafana → Explore → Loki → query `{compose_service="compliance-backend"}` → expect lines.
Filter by `job_id`: `{compose_service="compliance-backend"} | json | job_id="<id>"`.

## Dashboards

Provisioned automatically from `infrastructure/grafana/dashboards/*.json`.
Find them in Grafana → Dashboards → Compliance folder:
- **Pipeline** — per-step duration p50/p95/p99 + throughput
- **LLM** — call rate, escalation rate, latency by model
- **API** — RPS, latency p50/p95/p99, error rate per route
- **Errors** — ERROR-level log rate + recent ERROR lines (Loki)

## Production deploy (Contabo VPS)

Append to the existing `docker compose up -d` step in `infrastructure/contabo/README.md`:

```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml pull
docker compose -f docker-compose.yml -f docker-compose.observability.yml up -d
```

Expose only Grafana through the Cloudflare Tunnel (Loki, Prom, GlitchTip stay internal). Configure a separate hostname `grafana.compliance.<domain>` in the Cloudflare Tunnel.

## Cost / RAM budget

| Service | RAM (idle / typical) |
|---|---|
| GlitchTip web + worker + Postgres + Redis | ~700 MB |
| Loki + Promtail | ~200 MB |
| Prometheus (14 d retention) | ~300 MB |
| Grafana | ~150 MB |
| **Total** | **~1.4 GB** |

Contabo VPS spec must have ≥4 GB RAM headroom on top of app stack.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `up{job="compliance-backend"} == 0` in Prom | App not on `compliance-net` | `docker network connect compliance-net compliance-backend` |
| GlitchTip web 500 on `/issues` | DB migrations didn't run | `docker compose run --rm glitchtip-web ./manage.py migrate` |
| Grafana shows "No data" on Errors panel | Promtail not reading Docker logs | Check `docker logs promtail` for permission errors on `/var/run/docker.sock` |
| `pipeline_step_duration_seconds_count` always 0 | Pipeline never ran | Trigger one upload through `/upload` |
| Sentry SDK warns about missing source maps on `next build` | Expected without `SENTRY_AUTH_TOKEN` | Set token only in production CI |
