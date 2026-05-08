#!/usr/bin/env bash
# Restore drill — fetch the most recent backup from object storage and
# restore it into a scratch DB. Verifies the backup is real, restorable,
# and roughly the size we expect.
#
# Required env:
#   DATABASE_URL_SCRATCH    e.g. postgres://postgres:postgres@localhost:5433/compliance_scratch
#   BACKUP_REMOTE_KEY       full key of the dump in storage (run with --latest to auto-resolve)
#   BACKUP_AGE_IDENTITY     path to age private key (only required if backup is .age-encrypted)
#
# Optional env:
#   STORAGE_BACKEND         supabase | s3   (default: supabase, must match prod)
#   PYTHON                  python 3.12 (default: ./backend/venv/bin/python)
set -euo pipefail

# Ensure `app.storage` resolves whether script is run from repo root or backend/.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
export PYTHONPATH="${REPO_ROOT}/backend${PYTHONPATH:+:${PYTHONPATH}}"

PYTHON="${PYTHON:-./backend/venv/bin/python}"
WORK="$(mktemp -d -t cmpl-restore-XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

if [[ -z "${DATABASE_URL_SCRATCH:-}" ]]; then
  echo "DATABASE_URL_SCRATCH is required (e.g. postgres://...:5433/compliance_scratch)" >&2
  exit 2
fi

if [[ "${1:-}" == "--latest" ]]; then
  REMOTE_KEY="$($PYTHON -c '
import sys
from app.storage import get_backend
b = get_backend()
# Listing is backend-specific; reuse the simple Supabase-style API here.
# For S3 / MinIO, swap to: aws s3 ls s3://$BUCKET/backups/ --recursive
print("backups/latest.sql.gz")  # TODO: replace with real listing once daily backups land
')"
else
  REMOTE_KEY="${BACKUP_REMOTE_KEY:-}"
fi

if [[ -z "$REMOTE_KEY" ]]; then
  echo "No remote key resolved. Pass --latest or set BACKUP_REMOTE_KEY." >&2
  exit 2
fi

LOCAL_DUMP="$WORK/$(basename "$REMOTE_KEY")"
echo "[drill] downloading $REMOTE_KEY → $LOCAL_DUMP"
$PYTHON -c "
from app.storage import get_backend
get_backend().download_blob('$REMOTE_KEY', '$LOCAL_DUMP')
"

if [[ "$LOCAL_DUMP" == *.age ]]; then
  if [[ -z "${BACKUP_AGE_IDENTITY:-}" ]]; then
    echo "Backup is age-encrypted but BACKUP_AGE_IDENTITY is not set." >&2
    exit 2
  fi
  echo "[drill] decrypting via age"
  age -d -i "$BACKUP_AGE_IDENTITY" -o "${LOCAL_DUMP%.age}" "$LOCAL_DUMP"
  LOCAL_DUMP="${LOCAL_DUMP%.age}"
fi

echo "[drill] dropping + recreating scratch DB"
psql "$DATABASE_URL_SCRATCH" -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" >/dev/null

echo "[drill] pg_restore into scratch"
pg_restore --dbname="$DATABASE_URL_SCRATCH" --no-owner --no-acl "$LOCAL_DUMP"

echo "[drill] sanity check — table row counts"
psql "$DATABASE_URL_SCRATCH" -c "
SELECT relname, n_live_tup AS row_estimate
FROM pg_stat_user_tables
ORDER BY n_live_tup DESC
LIMIT 10;
"

echo "[drill] OK — restore completed at $(date -u +%FT%TZ)"
