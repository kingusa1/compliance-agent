#!/usr/bin/env bash
# Install LAW_OF_SKILLS v2 git hooks into THIS repo.
#
# Sets `core.hooksPath = .githooks` so the hooks travel with the repo
# (vs the default .git/hooks which is per-clone). Also marks them
# executable on POSIX. On Windows + Git Bash this works identically.
#
# Idempotent — running twice is safe.
set -eu

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

git config core.hooksPath .githooks
echo "set core.hooksPath = .githooks"

if [ -d ".githooks" ]; then
  for f in .githooks/*; do
    [ -f "$f" ] || continue
    chmod +x "$f" 2>/dev/null || true
    echo "marked +x: $f"
  done
else
  echo "WARN: .githooks/ not found" >&2
fi

echo ""
echo "Done. Verify with:"
echo "  git config core.hooksPath"
echo "  bash .githooks/pre-commit   # dry run (will run audit against staged diff)"
echo "  python scripts/doctrine/integrity.py verify"
