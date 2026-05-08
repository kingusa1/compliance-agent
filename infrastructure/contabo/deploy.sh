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
