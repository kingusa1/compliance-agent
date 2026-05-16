FROM python:3.12-slim

# System libs needed by psycopg2-binary, asyncpg, age (encryption for backups),
# and curl (used by railway healthcheck + ad-hoc pg_dump).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
    build-essential \
    libpq5 \
    curl \
    postgresql-client \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code AND alembic migrations so `alembic upgrade head` can
# run inside the container at deploy time. alembic.ini sits at /app root.
COPY app/ app/
COPY scripts/ scripts/
COPY alembic/ alembic/
COPY alembic.ini .

# Convenience shim so containers without a writable cwd still have somewhere
# to drop temp uploads. Production writes straight to Supabase Storage.
RUN mkdir -p uploads

# Railway injects $PORT at runtime — bind to it. Falls back to 8001 for local
# `docker run` without -e PORT. PYTHONUNBUFFERED makes uvicorn logs show up
# immediately in Railway's log stream instead of being held in stdout buffers.
ENV PORT=8001 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

EXPOSE 8001

# Run alembic migrations on container start, then exec uvicorn so PID 1 is
# uvicorn (clean SIGTERM handling for Railway's 15s grace window). The
# `exec` keeps signal forwarding intact; without it uvicorn would be a
# child of /bin/sh and miss SIGTERM.
CMD sh -c "(alembic upgrade head 2>&1 | tail -40 || echo 'ALEMBIC_FAILED — boot continues in degraded mode (see /readyz)') & exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --loop uvloop --http httptools --no-access-log --proxy-headers --forwarded-allow-ips='*'"
