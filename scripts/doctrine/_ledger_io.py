"""Shared parser + writer for ``BRAIN/06_Operations/Skill_Ledger.md``.

One canonical implementation imported by both ``audit.py`` and
``ledger.py``. Replaces the divergent regex pair from v2.1 first-cut
which caused the Active block to over-capture into History (CRITICAL
issue caught by python-reviewer + manifested as misplaced rows in the
live ledger).

Approach: section-bounded slicing instead of regex over the whole file.
Find the byte range of the Active session block (between its `##`
heading and the next `##`/`---` boundary), then parse / append within
that bounded substring. The History block is left untouched.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LEDGER_PATH = REPO_ROOT / "BRAIN" / "06_Operations" / "Skill_Ledger.md"


# Hardened against:
#   - re.S/regex over-capture across section boundaries
#   - duplicate header rows (Active and History have identical headers)
#   - prose / code blocks injected between table rows


def find_active_block(text: str) -> tuple[int, int]:
    """Return (start, end) byte indices of the Active session block.

    ``start`` is the index of the first character AFTER the heading
    line. ``end`` is the index of the first character of the next
    ``## ``-prefixed heading or ``---`` horizontal-rule line, whichever
    comes first. The boundary is exclusive on both ends so the slice
    text[start:end] contains only the section body.
    """
    m = re.search(r"^##\s+Active session\b[^\n]*\n", text, re.M)
    if not m:
        raise ValueError("Active session heading not found in ledger")
    start = m.end()
    rest = text[start:]
    end_m = re.search(r"\n(?:##\s|---\s*$)", rest, re.M)
    end = start + (end_m.start() if end_m else len(rest))
    return start, end


def find_history_block(text: str) -> tuple[int, int]:
    """Same shape as find_active_block but for the History section."""
    m = re.search(r"^##\s+History\b[^\n]*\n", text, re.M)
    if not m:
        raise ValueError("History heading not found in ledger")
    start = m.end()
    rest = text[start:]
    end_m = re.search(r"\n(?:##\s|---\s*$)", rest, re.M)
    end = start + (end_m.start() if end_m else len(rest))
    return start, end


# Markdown pipe-table row parsing. Columns by current ledger contract:
# | timestamp | session | skill | role | task-id | status | evidence |
LEDGER_COLUMNS = ("timestamp", "session", "skill", "role", "task_id", "status", "evidence")


def _is_separator_row(cells: list[str]) -> bool:
    """A `|---|---|...|` alignment row contains only `-`, `:`, and ` `."""
    if not cells:
        return True
    return all(set(c.strip()) <= set("-: ") for c in cells if c.strip())


def _is_header_row(cells: list[str]) -> bool:
    if not cells:
        return True
    return cells[0].strip().lower() in {"timestamp", "file", "skill"}


def _parse_pipe_row(line: str) -> list[str] | None:
    """Parse a pipe-table row line into cell list. Returns None if not
    a table row (used to skip prose between blocks)."""
    stripped = line.strip()
    if not stripped.startswith("|"):
        return None
    # Trim leading/trailing pipe + split
    inner = stripped.strip("|")
    cells = [c.strip() for c in inner.split("|")]
    return cells


def parse_active_rows(text: str) -> list[dict[str, str]]:
    """Return the list of data rows (header + separator filtered out)
    in the Active session block as dicts keyed by LEDGER_COLUMNS."""
    start, end = find_active_block(text)
    block = text[start:end]
    rows: list[dict[str, str]] = []
    for line in block.splitlines():
        cells = _parse_pipe_row(line)
        if cells is None:
            continue
        if _is_separator_row(cells) or _is_header_row(cells):
            continue
        if len(cells) < len(LEDGER_COLUMNS):
            # Tolerate fewer columns by padding; never write back though
            cells = cells + [""] * (len(LEDGER_COLUMNS) - len(cells))
        rows.append({col: cells[i] for i, col in enumerate(LEDGER_COLUMNS)})
    return rows


def append_active_row(text: str, row_text: str) -> str:
    """Insert ``row_text`` (a fully-formed `| ... |\\n` markdown row)
    after the last pipe-table line in the Active session block.

    Returns the new file text. Raises ValueError if Active block has
    no table header yet — caller must ensure the ledger has its
    skeleton before appending.
    """
    if not row_text.endswith("\n"):
        row_text += "\n"
    start, end = find_active_block(text)
    block = text[start:end]
    lines = block.splitlines(keepends=True)
    last_pipe = -1
    for i, line in enumerate(lines):
        if line.lstrip().startswith("|"):
            last_pipe = i
    if last_pipe < 0:
        raise ValueError(
            "Active session has no pipe-table — append the header row first"
        )
    lines.insert(last_pipe + 1, row_text)
    new_block = "".join(lines)
    return text[:start] + new_block + text[end:]


def sanitize_cell(value: str) -> str:
    """Strip pipe + CR + LF from a value before writing it into a
    markdown table cell. Defeats row-injection via newline/pipe in
    user-supplied content (the --waive reason was the canonical
    attack vector caught by security-reviewer)."""
    if value is None:
        return ""
    out = str(value)
    out = out.replace("\r", " ").replace("\n", " ").replace("|", "/")
    # Collapse runs of whitespace introduced by the replacements
    out = re.sub(r"\s+", " ", out).strip()
    return out
