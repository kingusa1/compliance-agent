#!/usr/bin/env bash
# Apply branch protection rules to main. Idempotent.
# Requires gh CLI authenticated with `repo` scope.
#
# Re-run after editing this script to push updated config.
set -euo pipefail

OWNER="${OWNER:-ArcadeTechLTD}"
REPO="${REPO:-compliance-agent}"
BRANCH="${BRANCH:-main}"

if ! command -v gh >/dev/null 2>&1; then
  echo "gh CLI not found. Install: https://cli.github.com" >&2
  exit 2
fi

echo "[bp] applying protection to $OWNER/$REPO/$BRANCH"

gh api \
  --method PUT \
  -H "Accept: application/vnd.github+json" \
  "/repos/$OWNER/$REPO/branches/$BRANCH/protection" \
  -f "required_status_checks[strict]=true" \
  -f "required_status_checks[contexts][]=pytest" \
  -f "required_status_checks[contexts][]=vitest" \
  -f "required_status_checks[contexts][]=coverage" \
  -f "required_status_checks[contexts][]=touched-fns-gate" \
  -F "enforce_admins=false" \
  -f "required_pull_request_reviews[required_approving_review_count]=1" \
  -F "required_pull_request_reviews[dismiss_stale_reviews]=true" \
  -F "required_pull_request_reviews[require_code_owner_reviews]=false" \
  -F "restrictions=" \
  -F "required_linear_history=true" \
  -F "allow_force_pushes=false" \
  -F "allow_deletions=false" \
  -F "required_conversation_resolution=true" \
  -F "lock_branch=false" \
  -F "allow_fork_syncing=false"

echo "[bp] OK — protection applied to $BRANCH"
