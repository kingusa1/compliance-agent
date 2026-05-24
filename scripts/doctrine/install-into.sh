#!/usr/bin/env bash
# Port the LAW_OF_SKILLS v2 doctrine + enforcement to another repo.
#
# Usage:
#   bash scripts/doctrine/install-into.sh /path/to/target-repo
#
# Copies (will not overwrite if files already exist — caller must remove
# first to force):
#   BRAIN/00_LAW_OF_SKILLS.md
#   BRAIN/00_LAW_OF_ENTERPRISE_GRADE.md
#   BRAIN/06_Operations/{Skill_Routing,Skill_Ledger,Session_Self_Audit,Doctrine_Integrity}.md
#   scripts/doctrine/{audit,ledger,metrics,integrity}.py
#   scripts/doctrine/install-hooks.sh
#   .githooks/{pre-commit,pre-push}
#
# Then runs scripts/doctrine/install-hooks.sh inside the target.
set -eu

SRC="$(cd "$(dirname "$0")/../.." && pwd)"
TARGET="${1:-}"
if [ -z "$TARGET" ] || [ ! -d "$TARGET" ]; then
  echo "usage: install-into.sh /path/to/target-repo" >&2
  exit 2
fi

cd "$TARGET"

copy_if_missing() {
  local rel="$1"
  local src="$SRC/$rel"
  local dst="$TARGET/$rel"
  if [ ! -f "$src" ]; then
    echo "  skip (missing in source): $rel"
    return
  fi
  mkdir -p "$(dirname "$dst")"
  if [ -e "$dst" ]; then
    echo "  exists (keeping): $rel"
  else
    cp "$src" "$dst"
    echo "  copied: $rel"
  fi
}

echo "Installing LAW_OF_SKILLS v2 doctrine into $TARGET ..."
copy_if_missing "BRAIN/00_LAW_OF_SKILLS.md"
copy_if_missing "BRAIN/00_LAW_OF_ENTERPRISE_GRADE.md"
copy_if_missing "BRAIN/06_Operations/Skill_Routing.md"
copy_if_missing "BRAIN/06_Operations/Skill_Ledger.md"
copy_if_missing "BRAIN/06_Operations/Session_Self_Audit.md"
copy_if_missing "BRAIN/06_Operations/Doctrine_Integrity.md"
copy_if_missing "BRAIN/06_Operations/Skill_Metrics.md"
copy_if_missing "BRAIN/06_Operations/Retroactive_Review_Queue.md"
copy_if_missing "scripts/doctrine/audit.py"
copy_if_missing "scripts/doctrine/ledger.py"
copy_if_missing "scripts/doctrine/metrics.py"
copy_if_missing "scripts/doctrine/integrity.py"
copy_if_missing "scripts/doctrine/install-hooks.sh"
copy_if_missing "scripts/doctrine/install-into.sh"
copy_if_missing ".githooks/pre-commit"
copy_if_missing ".githooks/pre-push"

bash "$TARGET/scripts/doctrine/install-hooks.sh"

echo ""
echo "Next steps inside $TARGET:"
echo "  1. Review BRAIN/00_LAW_OF_SKILLS.md — replace 'kingusa1 <IT@bbmgroup.io>' with this repo's identity."
echo "  2. Edit scripts/doctrine/audit.py — update EXPECTED_GIT_NAME/EMAIL constants."
echo "  3. Edit BRAIN/06_Operations/Skill_Routing.md — port the deterministic trigger table."
echo "  4. Run: python scripts/doctrine/integrity.py bless --reason 'initial doctrine baseline'"
echo "  5. Commit: git add . && git commit -m 'docs(doctrine): install LAW_OF_SKILLS v2'"
