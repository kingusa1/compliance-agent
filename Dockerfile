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

RUN mkdir -p uploads

ENV PORT=8001 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

EXPOSE 8001

CMD sh -c "(alembic upgrade head 2>&1 | tail -40 || echo 'ALEMBIC_FAILED — boot continues in degraded mode (see /readyz)') & exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --proxy-headers --forwarded-allow-ips='*'"
