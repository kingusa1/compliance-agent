## Repo-root Dockerfile for Railway's GitHub auto-deploy.
##
## Railway's git integration uses the repo root as the build context, so we
## need a Dockerfile here that knows how to build the FastAPI service from
## ./backend. The canonical Dockerfile lives at backend/Dockerfile and is
## still used by `railway up --service=compliance-agent --ci` from inside
## backend/. Keep the two in lockstep.
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
    build-essential \
    libpq5 \
    curl \
    postgresql-client \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app/ app/
COPY backend/scripts/ scripts/
COPY backend/alembic/ alembic/
COPY backend/alembic.ini .

# Phase-2 supplier-script markdown extracts. Required by
# app.watt_compliance.supplier_seed.docs_dir() so the script-checkpoint
# extractor + initial seed can read them on the Railway image.
# docs_dir() resolves `parents[3] / ".planning" / "phase2-docs"`; in the
# container that lands at `/.planning/phase2-docs`. Copy them there.
COPY .planning/phase2-docs/ /.planning/phase2-docs/

RUN mkdir -p uploads

ENV PORT=8001 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

EXPOSE 8001

# 2026-05-16 perf — explicit --loop uvloop --http httptools enables the
# C-extension event loop + HTTP parser shipped with uvicorn[standard].
# uvloop alone is ~2-3× faster than asyncio's default selector loop for
# the proxy-heavy I/O profile this service has (DB pool + Supabase
# Storage + OpenRouter outbound).
#
# --no-access-log: every Railway request was being JSON-logged, adding
# ~30-80ms per request to stdout buffering. The Prometheus instrumentator
# + Sentry already give us per-route latency + error visibility, so the
# uvicorn access log is redundant noise on prod.
#
# Workers stays at 1 because realtime.py uses in-memory asyncio.Queue
# pub/sub keyed on call_id — moving to >1 worker would silently break
# SSE delivery (each worker has its own queue and the publisher only
# fans out within its own process). Wait for Redis pub/sub to ship before
# scaling workers.
CMD sh -c "(alembic upgrade head 2>&1 | tail -40 || echo 'ALEMBIC_FAILED — boot continues in degraded mode (see /readyz)') & exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --loop uvloop --http httptools --no-access-log --proxy-headers --forwarded-allow-ips='*'"
