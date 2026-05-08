#!/bin/bash
# Start development environment for Compliance Agent v1 → v1.1
# Run this at the start of every Claude session.
#
# Boots:
#   - backend  FastAPI on :8001  (uvicorn --reload)
#   - frontend Next.js  on :3000 (npm run dev)
# Dependencies: Python 3.12 venv, Node 22, Supabase env in backend/.env

set -e
cd "$(dirname "$0")"

echo "================================================"
echo "  Compliance Agent — dev environment"
echo "  repo: $(pwd)"
echo "================================================"

# Kill any dev server lingering from the OLD repo path so localhost:3000
# always serves THIS repo. Common foot-gun: starting `npm run dev` from
# /Users/gomaa/Documents/Compliance-Agent/frontend-v3 (original) instead
# of /Users/gomaa/Documents/Compliance/frontend-v3 (current) — the
# missing Wave 3 ReanalyzeButton was the symptom in 2026-05-08 UAT.
if pgrep -fl "Compliance-Agent/frontend-v3.*next dev" >/dev/null 2>&1; then
  echo "→ killing wrong-repo Next.js dev server (Compliance-Agent/...)"
  pkill -f "Compliance-Agent/frontend-v3.*next dev" || true
  sleep 2
fi
if pgrep -fl "Compliance-Agent/frontend-v3" >/dev/null 2>&1; then
  echo "→ killing wrong-repo frontend helpers"
  pkill -f "Compliance-Agent/frontend-v3" || true
  sleep 1
fi

# ---- backend ----
if [ ! -d backend/venv ]; then
  echo "→ creating backend venv (first run)"
  python3 -m venv backend/venv
  ./backend/venv/bin/pip install --upgrade pip
  ./backend/venv/bin/pip install -r backend/requirements.txt
fi

if [ ! -f backend/.env ]; then
  echo "✗ backend/.env missing — copy from secrets store before continuing"
  exit 1
fi

echo "→ starting backend on :8001"
(cd backend && ./venv/bin/uvicorn app.main:app --port 8001 --reload --host 127.0.0.1) &
BACKEND_PID=$!
echo "  backend pid=$BACKEND_PID"

# ---- frontend ----
if [ ! -d frontend-v3/node_modules ]; then
  echo "→ installing frontend deps (first run)"
  (cd frontend-v3 && npm install)
fi

echo "→ starting frontend on :3000"
(cd frontend-v3 && npm run dev) &
FRONTEND_PID=$!
echo "  frontend pid=$FRONTEND_PID"

echo "================================================"
echo "  backend  http://127.0.0.1:8001/docs"
echo "  frontend http://127.0.0.1:3000"
echo "  Ctrl-C to stop both"
echo "================================================"

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null" EXIT
wait
