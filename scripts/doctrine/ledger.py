#!/usr/bin/env python3
"""LAW_OF_SKILLS v2.1 — Skill_Ledger CLI helper.

Appends one row to the Active session block of
``BRAIN/06_Operations/Skill_Ledger.md`` immediately after a `Skill`
or `Agent` tool call returns.

Backed by ``_ledger_io.py`` so audit.py and ledger.py share ONE
parser/writer implementation — eliminating the v2.1 first-cut bug
where the regex over-captured into History and append rows landed
at end-of-file.

All user-supplied cell values are sanitised (strip pipe + CR/LF)
to defeat row-injection (security-reviewer C1).

Usage:
  python scripts/doctrine/ledger.py append \\
      --skill python-reviewer --role verification \\
      --task-id bulk-fix-ui --status success \\
      --evidence "agent summary 0xabcd, 3 CRITICAL findings"

  python scripts/doctrine/ledger.py rotate-active --slug <session-slug>
  python scripts/doctrine/ledger.py list-active
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "doctrine"))
from _ledger_io import (  # noqa: E402
    LEDGER_PATH,
    append_active_row,
    find_history_block,
    parse_active_rows,
    sanitize_cell,
)

ROW_HEADER = (
    "| timestamp | session | skill | role | task-id | status | evidence |\n"
    "|---|---|---|---|---|---|---|\n"
)

VALID_ROLES = frozenset({"primary", "parallel", "verification", "auto-trigger", "waiver"})
VALID_STATUSES = frozenset({"success", "error", "skipped", "waived"})


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


def _ensure_ledger_path_safe() -> None:
    """Refuse if the ledger has been replaced with a symlink — defeats
    arbitrary-file-write via symlink swap (security-reviewer M7)."""
    if LEDGER_PATH.exists() and LEDGER_PATH.is_symlink():
        raise SystemExit(
            f"SECURITY: {LEDGER_PATH.relative_to(REPO_ROOT)} is a symlink — refusing to write"
        )


def _atomic_write(path: Path, content: str) -> None:
    """Write via temp + rename so a Ctrl-C / OOM / lock race never
    leaves a partially-written ledger."""
    _ensure_ledger_path_safe()
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


def _read_ledger() -> str:
    if not LEDGER_PATH.exists():
        raise SystemExit(f"Ledger missing: {LEDGER_PATH}")
    return LEDGER_PATH.read_text(encoding="utf-8")


def cmd_append(args: argparse.Namespace) -> int:
    if args.role not in VALID_ROLES:
        raise SystemExit(
            f"Invalid role {args.role!r}; expected one of {sorted(VALID_ROLES)}"
        )
    status_prefix = args.status.split(":", 1)[0].strip().lower()
    if status_prefix not in VALID_STATUSES:
        raise SystemExit(
            f"Invalid status {args.status!r}; expected one of {sorted(VALID_STATUSES)} "
            "(may be followed by ': <detail>')"
        )

    text = _read_ledger()
    ts = _utcnow()
    cells = [
        sanitize_cell(ts),
        sanitize_cell(args.session or "active"),
        sanitize_cell(args.skill),
        sanitize_cell(args.role),
        sanitize_cell(args.task_id),
        sanitize_cell(args.status),
        sanitize_cell(args.evidence),
    ]
    row = "| " + " | ".join(cells) + " |\n"
    new_text = append_active_row(text, row)
    _atomic_write(LEDGER_PATH, new_text)
    print(f"appended: {row.strip()}")
    return 0


def cmd_rotate_active(args: argparse.Namespace) -> int:
    text = _read_ledger()
    rows = parse_active_rows(text)
    if not rows:
        print("no rows in active block; nothing to rotate", file=sys.stderr)
        return 0
    # Build the History sub-block.
    slug = sanitize_cell(args.slug or "unnamed-session")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    block_lines = [f"\n### {today} — {slug}\n", "\n", ROW_HEADER]
    for r in rows:
        cells = [
            r.get("timestamp", ""),
            r.get("session", ""),
            r.get("skill", ""),
            r.get("role", ""),
            r.get("task_id", ""),
            r.get("status", ""),
            r.get("evidence", ""),
        ]
        block_lines.append("| " + " | ".join(sanitize_cell(c) for c in cells) + " |\n")
    sub_block = "".join(block_lines)

    # 1. Replace Active body with just the empty header (keeps the
    #    section skeleton for the next session).
    from _ledger_io import find_active_block

    a_start, a_end = find_active_block(text)
    # Preserve the preamble (heading + blockquote + blank line) that
    # sits above the table. We do that by keeping everything up to the
    # FIRST table line, then injecting the header rows.
    active_body = text[a_start:a_end]
    lines = active_body.splitlines(keepends=True)
    first_pipe = next(
        (i for i, l in enumerate(lines) if l.lstrip().startswith("|")),
        None,
    )
    if first_pipe is None:
        new_active_body = active_body.rstrip("\n") + "\n\n" + ROW_HEADER
    else:
        preamble = "".join(lines[:first_pipe])
        # Trailing content (after the table) is usually a blank line
        # before the next ---; preserve it.
        rest_idx = len(lines)
        for i, l in enumerate(lines[first_pipe:], start=first_pipe):
            if not l.lstrip().startswith("|"):
                rest_idx = i
                break
        trailing = "".join(lines[rest_idx:])
        new_active_body = preamble + ROW_HEADER + trailing
    text_with_clean_active = text[:a_start] + new_active_body + text[a_end:]

    # 2. Insert sub_block at the END of the History section body.
    h_start, h_end = find_history_block(text_with_clean_active)
    history_body = text_with_clean_active[h_start:h_end]
    new_history_body = history_body.rstrip("\n") + "\n" + sub_block
    new_text = (
        text_with_clean_active[:h_start]
        + new_history_body
        + text_with_clean_active[h_end:]
    )

    _atomic_write(LEDGER_PATH, new_text)
    print(f"rotated {len(rows)} row(s) into history under '{slug}'")
    return 0


def cmd_list_active(_args: argparse.Namespace) -> int:
    text = _read_ledger()
    rows = parse_active_rows(text)
    if not rows:
        print("(no active session rows)")
        return 0
    for r in rows:
        print(
            f"  {r['timestamp']}  {r['skill']:<25}  {r['role']:<14}  "
            f"{r['task_id']:<24}  {r['status']}"
        )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("append", help="Append a row to Active session.")
    a.add_argument("--skill", required=True)
    a.add_argument("--role", required=True, choices=sorted(VALID_ROLES))
    a.add_argument("--task-id", required=True)
    a.add_argument(
        "--status",
        required=True,
        help='Start with one of: success / error / skipped / waived',
    )
    a.add_argument("--evidence", required=True)
    a.add_argument("--session", default=None)
    a.set_defaults(func=cmd_append)

    r = sub.add_parser("rotate-active", help="Move Active rows into History.")
    r.add_argument("--slug", required=True)
    r.set_defaults(func=cmd_rotate_active)

    sub.add_parser("list-active").set_defaults(func=cmd_list_active)

    args = ap.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
