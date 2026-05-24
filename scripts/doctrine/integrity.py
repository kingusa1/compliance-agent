#!/usr/bin/env python3
"""LAW_OF_SKILLS v2.1 — doctrine integrity verifier.

Computes SHA-256 hashes of the binding LAW files + scripts + hooks,
compares against the manifest in
``BRAIN/06_Operations/Doctrine_Integrity.md``, and refuses to pass if
a file changed without a matching changelog row.

Tamper-evidence: the manifest itself is in TRACKED_FILES; the
``verify`` command compares the WORKING-TREE file content against the
manifest as it existed at the last committed git revision (HEAD). If
someone edits both the LAW and the manifest without committing first,
the verify catches the drift because the in-tree manifest disagrees
with HEAD's manifest.

CLI uses argparse so multi-word ``--reason`` values aren't silently
truncated (v2.1 first-cut bug caught by python-reviewer).

Usage:
  python scripts/doctrine/integrity.py verify
  python scripts/doctrine/integrity.py bless --reason "<verbatim user quote>"
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "BRAIN" / "06_Operations" / "Doctrine_Integrity.md"
BRAIN = REPO_ROOT / "BRAIN"

TRACKED_FILES: list[Path] = [
    BRAIN / "00_LAW_OF_SKILLS.md",
    BRAIN / "00_LAW_OF_ENTERPRISE_GRADE.md",
    BRAIN / "06_Operations" / "Skill_Routing.md",
    BRAIN / "06_Operations" / "Session_Self_Audit.md",
    REPO_ROOT / "CLAUDE.md",
    REPO_ROOT / "scripts" / "doctrine" / "audit.py",
    REPO_ROOT / "scripts" / "doctrine" / "ledger.py",
    REPO_ROOT / "scripts" / "doctrine" / "metrics.py",
    REPO_ROOT / "scripts" / "doctrine" / "integrity.py",
    REPO_ROOT / "scripts" / "doctrine" / "_ledger_io.py",
    REPO_ROOT / ".githooks" / "pre-commit",
    REPO_ROOT / ".githooks" / "pre-push",
]

MANIFEST_HEADER_RE = re.compile(
    r"<!--\s*MANIFEST-BEGIN\s*-->\s*\n(.*?)\n<!--\s*MANIFEST-END\s*-->",
    re.S,
)
MANIFEST_ROW_RE = re.compile(r"\|\s*([^|]+?)\s*\|\s*`([a-f0-9]{64})`\s*\|")
_CREATED_RE = re.compile(r"^created:\s*(\d{4}-\d{2}-\d{2})", re.M)


def sha256_of(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_manifest_text(text: str) -> dict[str, str]:
    m = MANIFEST_HEADER_RE.search(text)
    if not m:
        return {}
    out: dict[str, str] = {}
    for row in m.group(1).splitlines():
        row = row.strip()
        if not row.startswith("|") or row.startswith("|---") or row.lower().startswith("| file"):
            continue
        m2 = MANIFEST_ROW_RE.match(row)
        if m2:
            out[m2.group(1).strip()] = m2.group(2).strip()
    return out


def current_working_tree_manifest() -> dict[str, str]:
    if not MANIFEST.exists():
        return {}
    return _parse_manifest_text(MANIFEST.read_text(encoding="utf-8"))


def head_committed_manifest() -> dict[str, str]:
    """Read the manifest as it existed at HEAD (the last committed
    revision). If the working tree manifest disagrees, drift is real."""
    try:
        proc = subprocess.run(
            ["git", "show", f"HEAD:BRAIN/06_Operations/Doctrine_Integrity.md"],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            return {}
        return _parse_manifest_text(proc.stdout)
    except FileNotFoundError:
        return {}


def _preserved_created() -> str:
    if MANIFEST.exists():
        m = _CREATED_RE.search(MANIFEST.read_text(encoding="utf-8"))
        if m:
            return m.group(1)
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _atomic_write(path: Path, content: str) -> None:
    if path.exists() and path.is_symlink():
        raise SystemExit(f"SECURITY: {path} is a symlink — refusing to write")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmpname = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        os.replace(tmpname, str(path))
    except Exception:
        try:
            os.unlink(tmpname)
        except OSError:
            pass
        raise


def render_manifest(hashes: dict[str, str], changelog: list[str]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    rows = ["| File | SHA-256 |", "|---|---|"]
    for fpath in TRACKED_FILES:
        rel = fpath.relative_to(REPO_ROOT).as_posix()
        digest = hashes.get(rel, "")
        rows.append(f"| {rel} | `{digest}` |")
    body = "\n".join(rows)
    changelog_md = "\n".join(changelog) if changelog else "_(empty)_"
    return f"""---
created: {_preserved_created()}
updated: {now}
tags: [operations, doctrine, integrity, tamper-evident]
---

# Doctrine Integrity — manifest + changelog

> Records SHA-256 hashes of every binding doctrine file. The pre-push
> hook calls `scripts/doctrine/integrity.py verify`; if any file has
> changed without a matching changelog row, the push is blocked.
>
> To legitimately edit a doctrine file: change it, run
> `python scripts/doctrine/integrity.py bless --reason "<why>"`, commit
> the integrity manifest in the same commit as the edit. The bless
> reason is the audit trail.
>
> Tamper-evidence: `verify` cross-checks the working-tree manifest
> against HEAD's committed manifest. Editing both the LAW and the
> manifest in the working tree (to fake hashes) without an intermediate
> commit fails the cross-check.

<!-- MANIFEST-BEGIN -->
{body}
<!-- MANIFEST-END -->

## Changelog

{changelog_md}
"""


def parse_changelog() -> list[str]:
    if not MANIFEST.exists():
        return []
    text = MANIFEST.read_text(encoding="utf-8")
    parts = text.split("## Changelog", 1)
    if len(parts) != 2:
        return []
    lines = [l for l in parts[1].splitlines() if l.strip()]
    return [l for l in lines if l.strip() and l.strip() != "_(empty)_"]


def cmd_verify() -> int:
    expected_working = current_working_tree_manifest()
    if not expected_working:
        print(
            "FAIL: Doctrine_Integrity.md missing or has no manifest. "
            "Run `integrity.py bless --reason 'initial manifest'` first.",
            file=sys.stderr,
        )
        return 2

    # 1. Cross-check working-tree manifest against HEAD's committed
    #    manifest. If the working tree was edited (e.g. to fake hashes),
    #    these will disagree on the changed rows.
    expected_head = head_committed_manifest()
    if expected_head:
        manifest_drift: list[str] = []
        for rel, want in expected_working.items():
            head_want = expected_head.get(rel)
            if head_want is not None and head_want != want:
                manifest_drift.append(
                    f"  - {rel}: HEAD={head_want[:12]}… working-tree={want[:12]}…"
                )
        if manifest_drift:
            print("FAIL: manifest drift detected (working tree disagrees with HEAD)")
            for line in manifest_drift:
                print(line)
            print(
                "\nThis means Doctrine_Integrity.md was edited without an intervening "
                "commit. Either revert the working-tree edit OR commit the previous "
                "bless first."
            )
            return 1

    # 2. Standard hash check.
    drift: list[tuple[str, str, str]] = []
    for fpath in TRACKED_FILES:
        rel = fpath.relative_to(REPO_ROOT).as_posix()
        actual = sha256_of(fpath)
        want = expected_working.get(rel, "")
        if actual != want:
            drift.append((rel, want[:12] or "(missing)", actual[:12] or "(missing)"))
    if drift:
        print("FAIL: doctrine drift detected")
        for rel, want, got in drift:
            print(f"  - {rel}: manifest={want}… actual={got}…")
        print(
            "\nTo accept these changes: run\n"
            '  python scripts/doctrine/integrity.py bless --reason "<why>"\n'
            "and commit Doctrine_Integrity.md in the same commit."
        )
        return 1
    print("PASS: doctrine integrity ok")
    return 0


def cmd_bless(reason: str) -> int:
    new_hashes = {
        fpath.relative_to(REPO_ROOT).as_posix(): sha256_of(fpath)
        for fpath in TRACKED_FILES
    }
    changelog = parse_changelog()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    changelog.insert(0, f"- **{now}** — {reason}")
    _atomic_write(MANIFEST, render_manifest(new_hashes, changelog))
    print(f"blessed: {MANIFEST.relative_to(REPO_ROOT)} ({len(new_hashes)} files)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("verify")
    b = sub.add_parser("bless")
    b.add_argument("--reason", required=True)
    args = ap.parse_args()
    if args.cmd == "verify":
        return cmd_verify()
    if args.cmd == "bless":
        return cmd_bless(args.reason)
    return 2


if __name__ == "__main__":
    sys.exit(main())
