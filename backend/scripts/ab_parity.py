"""A/B parity harness for Wave 4 cost flags.

Workflow:
  1. Pick N call_ids (CLI flag --sample-size, default 50; --calls a,b,c
     overrides for explicit list).
  2. For each call, run the analyze callable twice:
        baseline  = use_agent_analyzer=False, embedding_prefilter_enabled=False
        candidate = use_agent_analyzer=True,  embedding_prefilter_enabled=True
  3. Compare verdict.status. Compute parity %.
  4. Write JSON report to --out (default: ab-parity-report.json in cwd).
  5. Print summary; exit 0 if parity >= --threshold (default 98.0), else 1.

Designed to be invoked manually: real LLM costs apply on every call.
The unit tests mock the analyze callable so the harness logic is testable
without burning credits.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


@dataclass
class Verdict:
    call_id: str
    status: str
    score: float


BASELINE_FLAGS = {"use_agent_analyzer": False, "embedding_prefilter_enabled": False}
CANDIDATE_FLAGS = {"use_agent_analyzer": True, "embedding_prefilter_enabled": True}


def compute_parity(baseline: list[Verdict], candidate: list[Verdict]) -> dict:
    """Diff two verdict lists by call_id. Returns {parity_pct, matches,
    mismatches, diffs[]}.

    Both lists must be in the same order (call_id-aligned). Missing call
    ids on either side count as mismatches."""
    by_id_b = {v.call_id: v for v in baseline}
    by_id_c = {v.call_id: v for v in candidate}
    all_ids = sorted(by_id_b.keys() | by_id_c.keys())
    matches = 0
    diffs: list[dict] = []
    for cid in all_ids:
        b = by_id_b.get(cid)
        c = by_id_c.get(cid)
        if b is None or c is None or b.status != c.status:
            diffs.append({
                "call_id": cid,
                "baseline_status": b.status if b else None,
                "candidate_status": c.status if c else None,
                "baseline_score": b.score if b else None,
                "candidate_score": c.score if c else None,
            })
        else:
            matches += 1
    total = len(all_ids)
    parity_pct = (100.0 * matches / total) if total else 100.0
    return {
        "parity_pct": parity_pct,
        "matches": matches,
        "mismatches": len(diffs),
        "diffs": diffs,
    }


def run_ab(
    call_ids: Iterable[str],
    analyze: Callable[..., Verdict],
    out_path: str | Path,
) -> dict:
    """Run baseline + candidate flag profiles across call_ids; write report; return summary."""
    call_ids = list(call_ids)
    baseline = [analyze(cid, flags=BASELINE_FLAGS) for cid in call_ids]
    candidate = [analyze(cid, flags=CANDIDATE_FLAGS) for cid in call_ids]
    parity = compute_parity(baseline, candidate)

    report = {
        "sample_size": len(call_ids),
        "baseline_flags": BASELINE_FLAGS,
        "candidate_flags": CANDIDATE_FLAGS,
        **parity,
    }
    Path(out_path).write_text(json.dumps(report, indent=2))
    return report


def _live_analyze(call_id: str, *, flags: dict) -> Verdict:
    """Production analyze callable — invoked when the script runs against
    a real DB. Mutates env-driven settings, runs the same pipeline path
    used by `_step_analyze_checkpoints` (async) + `_step_score` + `_step_finalize`,
    returns the finalized verdict.

    `_step_analyze_checkpoints` is async; we drive it with asyncio.run.
    Wrapping fn stays sync so run_ab can call it serially without an event loop.

    NOT exercised by unit tests (they pass their own mock). Kept here so
    the CLI form is self-contained.
    """
    # Defer imports to avoid heavy startup cost on `pytest --collect-only`
    from app.config import settings
    from app.database import SessionLocal
    from app.models import Call
    from app.pipeline import _step_analyze_checkpoints, _step_score, _step_finalize

    settings.use_agent_analyzer = flags["use_agent_analyzer"]
    settings.embedding_prefilter_enabled = flags["embedding_prefilter_enabled"]

    db = SessionLocal()
    try:
        call = db.query(Call).filter(Call.id == call_id).first()
        if call is None:
            return Verdict(call_id=call_id, status="error_missing", score=0.0)
        analysis = asyncio.run(
            _step_analyze_checkpoints(call_id, {"transcript": call.transcript or ""}, db)
        )
        _step_score(call_id, analysis, db)
        _step_finalize(call_id, db)
        db.refresh(call)
        return Verdict(
            call_id=call_id,
            status=getattr(call, "compliance_status", "unknown") or "unknown",
            score=float(getattr(call, "score", 0.0) or 0.0),
        )
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="A/B parity harness for Wave 4 cost flags")
    p.add_argument("--sample-size", type=int, default=50, help="Number of recent calls to sample")
    p.add_argument("--calls", default=None, help="Comma-separated list of call_ids (overrides --sample-size)")
    p.add_argument("--out", default="ab-parity-report.json")
    p.add_argument("--threshold", type=float, default=98.0, help="Min parity %% to exit 0")
    args = p.parse_args(argv)

    if args.calls:
        call_ids = [s.strip() for s in args.calls.split(",") if s.strip()]
    else:
        from app.database import SessionLocal
        from app.models import Call
        db = SessionLocal()
        try:
            rows = (
                db.query(Call.id)
                .filter(Call.transcript.isnot(None))
                .filter(Call.script_id.isnot(None))
                .order_by(Call.id.desc())
                .limit(args.sample_size)
                .all()
            )
            call_ids = [r[0] for r in rows]
        finally:
            db.close()

    if not call_ids:
        print("No call_ids resolved. Pass --calls or seed sample data.", file=sys.stderr)
        return 2

    summary = run_ab(call_ids, analyze=_live_analyze, out_path=args.out)
    print(json.dumps({k: v for k, v in summary.items() if k != "diffs"}, indent=2))
    print(f"Report: {args.out} ({summary['mismatches']} mismatch(es) recorded)")
    return 0 if summary["parity_pct"] >= args.threshold else 1


if __name__ == "__main__":
    sys.exit(main())
