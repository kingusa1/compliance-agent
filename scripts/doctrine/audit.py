#!/usr/bin/env python3
"""LAW_OF_SKILLS v2.1 — executable audit.

Greps `git diff` against the deterministic trigger table, reads
``BRAIN/06_Operations/Skill_Ledger.md`` and (when available) the
session transcript, and emits a verdict. Exits non-zero on FAIL so
git hooks can use it as a gate.

Modes:
  pre-commit  — staged-diff trigger check + secret grep. Ledger checks
                are skipped (the trio + tool calls fire AFTER the
                first commit, before push).
  pre-push    — committed-range trigger check + ledger integrity +
                secret grep + identity verification + alembic sanity.
                This is the hard gate.
  session-end — full audit including transcript-vs-ledger drift
                (requires ``--transcript <path>``).

Hard-fail checks (cannot be waived):
  - secret-scan (leaked API keys in diff)
  - git-identity (must be kingusa1 <IT@bbmgroup.io>)
  - alembic (single head, ≤32 char revision)

Soft-fail checks (waivable per LAW Rule 7):
  - trigger-not-ledgered (auto-trigger fired but missing reviewer ledger row)
  - ledger-drift (transcript invocation absent from ledger)

Usage:
  python scripts/doctrine/audit.py pre-commit
  python scripts/doctrine/audit.py pre-push --since origin/main
  python scripts/doctrine/audit.py session-end --transcript transcript.md
  python scripts/doctrine/audit.py pre-push --waive "Mohamed: skip review on docs-only change"
"""
from __future__ import annotations

import argparse
import json
import posixpath
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "doctrine"))
from _ledger_io import (  # noqa: E402  (path append is intentional)
    LEDGER_PATH,
    append_active_row,
    parse_active_rows,
    sanitize_cell,
)

# ── Deterministic trigger table (mirror of LAW_OF_SKILLS v2.1).
#    Each entry maps a file-path regex to a tool that MUST appear in
#    either the transcript or the Skill_Ledger.
TRIGGERS: list[dict[str, str]] = [
    # backend/.*\.py but NOT backend/<anything-without-slash>/.../tests/...
    {
        "pattern": r"^backend/(?!(?:[^/]+/)*tests/).*\.py$",
        "tool": "python-reviewer",
        "kind": "Agent",
    },
    {
        "pattern": r"^frontend-v3/src/(?!.*tests/e2e/).*\.(ts|tsx)$",
        "tool": "code-reviewer",
        "kind": "Agent",
    },
    {
        "pattern": r"^backend/alembic/versions/.*\.py$",
        "tool": "database-reviewer",
        "kind": "Agent",
    },
    {
        "pattern": r"^backend/.*\.go$",
        "tool": "go-reviewer",
        "kind": "Agent",
    },
    {
        "pattern": r"^(android|mobile)/.*\.kt$",
        "tool": "kotlin-reviewer",
        "kind": "Agent",
    },
]

# Auth/security trigger fires when a diff ADDS one of these patterns.
AUTH_DIFF_RE = re.compile(
    r"^\+.*Depends\(\s*(current_user|current_reviewer|_require_admin|require_admin)\b"
)

# Anthropic SDK trigger — file imports anthropic / @anthropic-ai/sdk.
ANTHROPIC_IMPORT_RE = re.compile(
    r"^\+.*(?:import\s+anthropic|from\s+anthropic\b|from\s+['\"]@anthropic-ai/sdk['\"])"
)

# Secret detection — covers OpenAI, Anthropic, OpenRouter, Vercel, Slack,
# GitHub (PAT/OAuth/App), AWS access key, GitLab, Stripe, SendGrid,
# Stripe webhook, generic JWT (Supabase service_role).
SECRET_REGEX = re.compile(
    r"("
    r"sk-(?:proj|or-v1|live|ant)-[A-Za-z0-9_-]{16,}"
    r"|sk_live_[A-Za-z0-9]{16,}"
    r"|vcp_[A-Za-z0-9]{20,}"
    r"|xoxb-[A-Za-z0-9-]{20,}"
    r"|gh[psoru]_[A-Za-z0-9]{20,}"
    r"|AKIA[A-Z0-9]{16}"
    r"|glpat-[A-Za-z0-9_-]{20,}"
    r"|rk_(?:live|test)_[A-Za-z0-9]{24,}"
    r"|whsec_[A-Za-z0-9]{32,}"
    r"|SG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}"
    r"|eyJhbGci[A-Za-z0-9_=-]{40,}\."  # JWT (Supabase service_role, etc.)
    r")",
)

EXPECTED_GIT_NAME = "kingusa1"
EXPECTED_GIT_EMAIL = "IT@bbmgroup.io"

# Checks whose FAIL severity is NOT waivable via --waive.
NON_WAIVABLE_CHECKS = frozenset({"secret-scan", "git-identity", "alembic", "doctrine-integrity"})

VERDICT_PASS = 0
VERDICT_FAIL = 1
VERDICT_PREREQ = 2


@dataclass
class AuditFinding:
    severity: str  # "FAIL" or "WARN"
    check: str
    detail: str

    def render(self) -> str:
        return f"  {self.severity}: [{self.check}] {self.detail}"


def run_git(*args: str, allow_failure: bool = False) -> str:
    """Run a git command in REPO_ROOT. Raises SystemExit on non-zero
    unless ``allow_failure=True``."""
    proc = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0 and not allow_failure:
        raise SystemExit(
            f"PREREQ: git {' '.join(args)} failed (rc={proc.returncode}): {proc.stderr.strip()}"
        )
    return proc.stdout or ""


def git_files_in_range(since: str) -> list[str]:
    out = run_git("diff", "--name-only", f"{since}..HEAD")
    return [l.strip() for l in out.splitlines() if l.strip()]


def git_staged_files() -> list[str]:
    out = run_git("diff", "--cached", "--name-only")
    return [l.strip() for l in out.splitlines() if l.strip()]


def git_diff_text(since: str | None) -> str:
    if since is None:
        return run_git("diff", "--cached")
    return run_git("diff", f"{since}..HEAD")


def _normalise_path(p: str) -> str:
    """Lowercase + forward-slash + collapse dot-segments — defeats
    case-insensitive-filesystem evasion + dot-segment evasion."""
    return posixpath.normpath(p.replace("\\", "/")).lower()


def files_match_triggers(files: Iterable[str]) -> set[str]:
    required: set[str] = set()
    for path in files:
        norm = _normalise_path(path)
        for trig in TRIGGERS:
            if re.search(trig["pattern"], norm, re.IGNORECASE):
                required.add(trig["tool"])
    return required


def diff_triggers_auth(diff_text: str) -> bool:
    return any(AUTH_DIFF_RE.match(line) for line in diff_text.splitlines())


def diff_triggers_anthropic(diff_text: str) -> bool:
    return any(ANTHROPIC_IMPORT_RE.search(line) for line in diff_text.splitlines())


def secrets_in_diff(diff_text: str) -> list[str]:
    hits: list[str] = []
    for line in diff_text.splitlines():
        if not line.startswith("+"):
            continue
        m = SECRET_REGEX.search(line)
        if m:
            hits.append(m.group(0)[:80])
    return hits


def ledger_active_invocations() -> set[str]:
    if not LEDGER_PATH.exists():
        return set()
    text = LEDGER_PATH.read_text(encoding="utf-8")
    rows = parse_active_rows(text)
    return {r["skill"] for r in rows if r.get("skill")}


def transcript_tool_calls(transcript_path: Path) -> set[str]:
    text = transcript_path.read_text(encoding="utf-8")
    skills = set(re.findall(r'<parameter name="skill">([^<]+)</parameter>', text))
    agents = set(
        re.findall(r'<parameter name="subagent_type">([^<]+)</parameter>', text)
    )
    return skills | agents


def git_identities_in_range(since: str) -> list[tuple[str, str]]:
    """Return (name, email) for every commit in since..HEAD. Empty list
    if the range is empty (no commits to verify)."""
    out = run_git("log", f"{since}..HEAD", "--format=%an%x00%ae")
    pairs: list[tuple[str, str]] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\x00", 1)
        if len(parts) == 2:
            pairs.append((parts[0], parts[1]))
    return pairs


def alembic_heads_ok() -> tuple[bool, str]:
    try:
        from alembic.config import Config  # type: ignore
        from alembic.script import ScriptDirectory  # type: ignore
    except Exception as exc:  # pragma: no cover
        return True, f"skipped (alembic not importable: {type(exc).__name__})"
    cfg_path = REPO_ROOT / "backend" / "alembic.ini"
    if not cfg_path.exists():
        return True, "skipped (no alembic.ini)"
    sd = ScriptDirectory.from_config(Config(str(cfg_path)))
    heads = sd.get_heads()
    if len(heads) != 1:
        return False, f"multiple heads: {heads}"
    bad = [r.revision for r in sd.walk_revisions() if len(r.revision) > 32]
    if bad:
        return False, f"revision id > 32 chars: {bad}"
    return True, heads[0]


# ────────────────────────────────────────────────────────────────────
# Modes
# ────────────────────────────────────────────────────────────────────


def mode_pre_commit() -> tuple[list[AuditFinding], list[str], set[str]]:
    findings: list[AuditFinding] = []
    files = git_staged_files()
    diff = git_diff_text(None)

    required = files_match_triggers(files)
    if diff_triggers_auth(diff):
        required.add("security-reviewer")
    if diff_triggers_anthropic(diff):
        required.add("claude-api")

    leaks = secrets_in_diff(diff)
    if leaks:
        findings.append(
            AuditFinding("FAIL", "secret-scan", f"{len(leaks)} candidate(s): {leaks[:3]}")
        )

    # pre-commit can't enforce ledger (work-in-progress), but warns.
    if required:
        findings.append(
            AuditFinding(
                "WARN",
                "trigger-fired",
                f"{sorted(required)} — must be invoked + ledgered before push",
            )
        )
    return findings, files, required


def mode_pre_push(since: str) -> tuple[list[AuditFinding], list[str], set[str]]:
    findings: list[AuditFinding] = []
    files = git_files_in_range(since)
    if not files:
        return findings, files, set()

    diff = git_diff_text(since)
    required = files_match_triggers(files)
    if diff_triggers_auth(diff):
        required.add("security-reviewer")
    if diff_triggers_anthropic(diff):
        required.add("claude-api")

    leaks = secrets_in_diff(diff)
    if leaks:
        findings.append(
            AuditFinding(
                "FAIL", "secret-scan", f"{len(leaks)} candidate(s): {leaks[:3]}"
            )
        )

    # Identity check on EVERY commit in range, not just the tip.
    for name, email in git_identities_in_range(since):
        if name != EXPECTED_GIT_NAME or email != EXPECTED_GIT_EMAIL:
            findings.append(
                AuditFinding(
                    "FAIL",
                    "git-identity",
                    f"commit by {name} <{email}> "
                    f"(expected {EXPECTED_GIT_NAME} <{EXPECTED_GIT_EMAIL}>)",
                )
            )
            break  # one is enough; don't spam

    # Alembic sanity — only if migrations touched.
    if any(
        f.endswith(".py") and "alembic/versions/" in f.replace("\\", "/").lower()
        for f in files
    ):
        ok, info = alembic_heads_ok()
        if not ok:
            findings.append(AuditFinding("FAIL", "alembic", info))

    ledger = ledger_active_invocations()
    missing = required - ledger
    if missing:
        findings.append(
            AuditFinding(
                "FAIL",
                "trigger-not-ledgered",
                f"Auto-trigger requires {sorted(missing)} but no Skill_Ledger row. "
                "Invoke now, append ledger row, retry push.",
            )
        )

    return findings, files, required


def mode_session_end(
    transcript_path: Path, since: str
) -> tuple[list[AuditFinding], list[str], set[str]]:
    findings, files, required = mode_pre_push(since)
    if not transcript_path.exists():
        findings.append(
            AuditFinding("FAIL", "transcript", f"missing transcript path: {transcript_path}")
        )
        return findings, files, required
    invoked = transcript_tool_calls(transcript_path)
    ledger = ledger_active_invocations()
    drift = invoked - ledger
    if drift:
        findings.append(
            AuditFinding(
                "FAIL",
                "ledger-drift",
                f"Tool calls in transcript but no ledger row: {sorted(drift)}",
            )
        )
    return findings, files, required


# ────────────────────────────────────────────────────────────────────
# Waiver handling — sanitised + non-waivable hard checks
# ────────────────────────────────────────────────────────────────────


def write_waiver_row(waived_reason: str, mode: str) -> None:
    """Append a waiver row. Raises SystemExit on failure so the caller
    never silently passes a waiver with no audit trail."""
    if not LEDGER_PATH.exists():
        raise SystemExit("PREREQ: Skill_Ledger.md missing — cannot record waiver")
    safe_reason = sanitize_cell(waived_reason)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    row = (
        f"| {ts} | audit-waiver | (n/a) | waiver | {mode} | "
        f"waived: {safe_reason} | scripts/doctrine/audit.py |\n"
    )
    text = LEDGER_PATH.read_text(encoding="utf-8")
    new_text = append_active_row(text, row)
    LEDGER_PATH.write_text(new_text, encoding="utf-8")


def compute_verdict(
    findings: list[AuditFinding], waive: str | None
) -> tuple[str, int]:
    fails = [f for f in findings if f.severity == "FAIL"]
    hard_fails = [f for f in fails if f.check in NON_WAIVABLE_CHECKS]
    if hard_fails:
        return "FAIL", VERDICT_FAIL
    if waive:
        return "WAIVED", VERDICT_PASS
    if fails:
        return "FAIL", VERDICT_FAIL
    return "PASS", VERDICT_PASS


def render_verdict(
    mode: str,
    verdict: str,
    findings: list[AuditFinding],
    files: list[str],
    required: set[str],
    invoked: set[str],
    waived_reason: str | None,
) -> str:
    lines: list[str] = []
    fails = [f for f in findings if f.severity == "FAIL"]
    warns = [f for f in findings if f.severity == "WARN"]
    lines.append(f"\n=== LAW_OF_SKILLS v2.1 audit — mode={mode} verdict={verdict} ===")
    lines.append(f"  files touched     : {len(files)}")
    lines.append(f"  triggers required : {sorted(required) or 'none'}")
    lines.append(f"  ledger invocations: {sorted(invoked) or 'none'}")
    if waived_reason:
        lines.append(f"  waiver            : {waived_reason[:80]}")
    if fails or warns:
        lines.append("  findings:")
        for f in fails + warns:
            lines.append(f.render())
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", choices=["pre-commit", "pre-push", "session-end"])
    ap.add_argument("--since", default="origin/main")
    ap.add_argument("--transcript", default=None)
    ap.add_argument(
        "--waive",
        default=None,
        help='Verbatim user quote authorising the skip. Stamps a "waived" '
        "row in the ledger. CANNOT override secret-scan / git-identity / "
        "alembic / doctrine-integrity — those are hard fails.",
    )
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.mode == "pre-commit":
        findings, files, required = mode_pre_commit()
    elif args.mode == "pre-push":
        findings, files, required = mode_pre_push(args.since)
    else:
        if not args.transcript:
            print("PREREQ: --transcript required for session-end mode", file=sys.stderr)
            return VERDICT_PREREQ
        findings, files, required = mode_session_end(Path(args.transcript), args.since)

    invoked = ledger_active_invocations()

    if args.waive:
        # Stamp the waiver row BEFORE computing verdict. If the write
        # fails, the function raises SystemExit and we never silently
        # pass a waiver with no audit trail.
        write_waiver_row(args.waive, args.mode)

    verdict_label, exit_code = compute_verdict(findings, args.waive)
    rendered = render_verdict(
        args.mode, verdict_label, findings, files, required, invoked, args.waive
    )

    if args.json:
        payload = {
            "mode": args.mode,
            "verdict": verdict_label,
            "files": files,
            "required": sorted(required),
            "invoked": sorted(invoked),
            "findings": [
                {"severity": f.severity, "check": f.check, "detail": f.detail}
                for f in findings
            ],
            "waived_reason": args.waive,
        }
        print(json.dumps(payload, indent=2))
    else:
        print(rendered)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
