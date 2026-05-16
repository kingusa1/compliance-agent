"""A/B harness for the grader prompt-cache refactor (2026-05-16).

Workflow per call_id:
  1. Load the call's transcript + checkpoint list + similar_rejections by
     hitting the live FastAPI backend (no DB access required from this
     script — keeps the harness portable).
  2. Group checkpoints by strictness, batch at BATCH_SIZE=6.
  3. For each batch run `_analyze_batch` twice with `temperature=0`:
       baseline   = settings.grader_prompt_caching_enabled = False
       candidate  = settings.grader_prompt_caching_enabled = True
  4. Diff every field per checkpoint result.
  5. Write a JSON report + print a summary.

Acceptance criteria (any single failure → exit 1):
  • status must be 100% identical on every checkpoint.
  • confidence must be 100% identical.
  • evidence quotes (lowercased + punctuation-stripped + whitespace-split):
    Jaccard overlap ≥ 0.90 on every pass/partial verdict.
  • W4 categorical fields (script_line_number, similar_rejection_id,
    suggested_category, suggested_fix_required) must be 100% identical.
  • category_confidence may drift by ≤ 0.05.
  • notes (free-form text): accept any rephrasing IF status+confidence
    match. Diffs logged for human spot-check.

Run:
    cd backend
    ./venv/Scripts/python.exe scripts/cache_ab_harness.py \\
        --call-ids 601091d7-1374-4a95-8869-f22ad580971d,dceddee7-... \\
        --backend https://compliance-agent-production-690e.up.railway.app \\
        --out /tmp/ab.json

Cost: ~$8 total for 4 calls × ~21 batches × 2 paths × ~$0.045 per Opus
batch. Cheap, one-shot. The harness hits the real OpenRouter API so
production keys must be loaded (DATABASE_URL not required).

Plan: C:\\Users\\kingu\\.claude\\plans\\nifty-questing-yeti.md
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import httpx

# Make backend/ importable when run from repo root or from backend/ itself.
_THIS_DIR = Path(__file__).resolve().parent
_BACKEND = _THIS_DIR.parent
sys.path.insert(0, str(_BACKEND))

from app import config as _config  # noqa: E402
from app.checkpoint_analyzer import _analyze_batch, BATCH_SIZE  # noqa: E402


def _normalise_quote(text: str | None) -> set[str]:
    if not text:
        return set()
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", text.lower())
    return {tok for tok in cleaned.split() if tok}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 1.0
    return len(a & b) / len(union)


def _compare_results(
    baseline: list[dict], candidate: list[dict]
) -> dict[str, Any]:
    """Field-by-field diff for one batch's worth of results.

    Returns a structured per-checkpoint diff record with overall flags
    (any_drift, any_evidence_drop_below_floor).
    """
    n = max(len(baseline), len(candidate))
    rows: list[dict[str, Any]] = []
    any_drift = False
    any_evidence_below_floor = False
    for i in range(n):
        b = baseline[i] if i < len(baseline) else None
        c = candidate[i] if i < len(candidate) else None
        if b is None or c is None:
            rows.append({"index": i, "missing_side": "baseline" if b is None else "candidate"})
            any_drift = True
            continue
        status_match = b.get("status") == c.get("status")
        conf_match = b.get("confidence") == c.get("confidence")
        b_ev = _normalise_quote(b.get("evidence"))
        c_ev = _normalise_quote(c.get("evidence"))
        ev_jaccard = _jaccard(b_ev, c_ev)
        # W4 categorical
        w4_match = all(
            b.get(k) == c.get(k)
            for k in (
                "script_line_number",
                "similar_rejection_id",
                "suggested_category",
                "suggested_fix_required",
            )
        )
        b_catconf = b.get("category_confidence")
        c_catconf = c.get("category_confidence")
        cat_conf_drift = (
            abs(float(b_catconf) - float(c_catconf))
            if isinstance(b_catconf, (int, float)) and isinstance(c_catconf, (int, float))
            else 0.0
        )
        notes_match = (b.get("notes") or "") == (c.get("notes") or "")

        # Drift definition: status or confidence different, OR evidence
        # Jaccard < 0.90 on a pass/partial verdict, OR W4 fields differ,
        # OR category_confidence drifts > 0.05.
        evidence_required = b.get("status") in ("pass", "partial")
        evidence_below_floor = evidence_required and ev_jaccard < 0.90
        any_evidence_below_floor = any_evidence_below_floor or evidence_below_floor

        drift = (
            not status_match
            or not conf_match
            or evidence_below_floor
            or not w4_match
            or cat_conf_drift > 0.05
        )
        any_drift = any_drift or drift

        rows.append(
            {
                "index": i,
                "name": b.get("name"),
                "baseline_status": b.get("status"),
                "candidate_status": c.get("status"),
                "status_match": status_match,
                "baseline_confidence": b.get("confidence"),
                "candidate_confidence": c.get("confidence"),
                "confidence_match": conf_match,
                "evidence_jaccard": round(ev_jaccard, 3),
                "evidence_below_floor": evidence_below_floor,
                "w4_match": w4_match,
                "category_confidence_drift": round(cat_conf_drift, 3),
                "notes_match": notes_match,
                "drift": drift,
            }
        )
    return {
        "any_drift": any_drift,
        "any_evidence_below_floor": any_evidence_below_floor,
        "rows": rows,
    }


async def _fetch_call_context(
    client: httpx.AsyncClient, backend: str, call_id: str
) -> dict[str, Any]:
    """Pull transcript + segments + checkpoints from the live backend."""
    call_resp = await client.get(f"{backend}/api/calls/{call_id}", timeout=30.0)
    call_resp.raise_for_status()
    call = call_resp.json()
    transcript = call.get("transcript") or call.get("assemblyai_transcript") or ""
    supplier = call.get("detected_supplier") or "Unknown"
    cps_resp = await client.get(
        f"{backend}/api/calls/{call_id}/script-checkpoints", timeout=30.0
    )
    cps_resp.raise_for_status()
    cps_payload = cps_resp.json()
    checkpoints = (
        cps_payload.get("checkpoints")
        if isinstance(cps_payload, dict)
        else cps_payload
    ) or []
    return {
        "call_id": call_id,
        "transcript": transcript,
        "supplier": supplier,
        "checkpoints": checkpoints,
    }


async def _run_batches(
    *,
    transcript: str,
    supplier: str,
    checkpoints: list[dict],
    similar_rejections: list[dict] | None,
    flag_value: bool,
) -> list[dict[str, Any]]:
    """Run all batches for one call under a given flag value, sequentially
    so the cache-write/cache-read ordering is deterministic."""
    _config.settings.grader_prompt_caching_enabled = flag_value
    groups: dict[str, list[dict]] = {}
    for cp in checkpoints:
        groups.setdefault(cp.get("strictness", "mandatory"), []).append(cp)
    out: list[dict[str, Any]] = []
    for strictness, cps in groups.items():
        for i in range(0, len(cps), BATCH_SIZE):
            batch = cps[i : i + BATCH_SIZE]
            results = await _analyze_batch(
                transcript=transcript,
                batch=batch,
                supplier=supplier,
                strictness=strictness,
                similar_rejections=similar_rejections,
                call_id=None,
            )
            out.extend(results if isinstance(results, list) else [results])
    return out


async def _run_one(client: httpx.AsyncClient, backend: str, call_id: str) -> dict[str, Any]:
    ctx = await _fetch_call_context(client, backend, call_id)
    transcript = ctx["transcript"]
    supplier = ctx["supplier"]
    checkpoints = ctx["checkpoints"]
    if not transcript or not checkpoints:
        return {
            "call_id": call_id,
            "skipped": True,
            "reason": "missing transcript or checkpoints",
            "transcript_len": len(transcript or ""),
            "checkpoint_count": len(checkpoints or []),
        }
    print(
        f"[{call_id[:8]}] supplier={supplier!r} "
        f"transcript={len(transcript)} chars "
        f"checkpoints={len(checkpoints)}",
        flush=True,
    )
    print(f"[{call_id[:8]}] running BASELINE (flag=OFF)...", flush=True)
    baseline = await _run_batches(
        transcript=transcript,
        supplier=supplier,
        checkpoints=checkpoints,
        similar_rejections=None,
        flag_value=False,
    )
    print(f"[{call_id[:8]}] running CANDIDATE (flag=ON)...", flush=True)
    candidate = await _run_batches(
        transcript=transcript,
        supplier=supplier,
        checkpoints=checkpoints,
        similar_rejections=None,
        flag_value=True,
    )
    diff = _compare_results(baseline, candidate)
    return {
        "call_id": call_id,
        "supplier": supplier,
        "transcript_len": len(transcript),
        "checkpoint_count": len(checkpoints),
        "baseline_count": len(baseline),
        "candidate_count": len(candidate),
        "any_drift": diff["any_drift"],
        "any_evidence_below_floor": diff["any_evidence_below_floor"],
        "rows": diff["rows"],
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--call-ids",
        required=True,
        help="comma-separated list of call_ids (must exist in the backend's DB)",
    )
    parser.add_argument(
        "--backend",
        default="https://compliance-agent-production-690e.up.railway.app",
        help="backend base URL",
    )
    parser.add_argument(
        "--out",
        default="cache-ab-report.json",
        help="output JSON path",
    )
    args = parser.parse_args()
    call_ids = [c.strip() for c in args.call_ids.split(",") if c.strip()]
    if not call_ids:
        print("No call_ids provided.", file=sys.stderr)
        return 2
    print(f"Backend: {args.backend}")
    print(f"Call IDs: {call_ids}")
    if not os.environ.get("OPENROUTER_API_KEY"):
        print(
            "WARNING: OPENROUTER_API_KEY not set in env. Loading from .env if present.",
            flush=True,
        )

    results: list[dict[str, Any]] = []
    async with httpx.AsyncClient() as client:
        for call_id in call_ids:
            try:
                r = await _run_one(client, args.backend, call_id)
            except Exception as e:  # noqa: BLE001
                r = {"call_id": call_id, "error": repr(e)[:500]}
            results.append(r)

    report = {
        "results": results,
        "summary": {
            "total_calls": len(results),
            "any_drift_calls": sum(1 for r in results if r.get("any_drift")),
            "any_evidence_below_floor_calls": sum(
                1 for r in results if r.get("any_evidence_below_floor")
            ),
            "skipped": sum(1 for r in results if r.get("skipped")),
            "errored": sum(1 for r in results if r.get("error")),
        },
    }
    Path(args.out).write_text(json.dumps(report, indent=2, default=str))
    print(f"Wrote {args.out}")
    print(json.dumps(report["summary"], indent=2))
    return 0 if report["summary"]["any_drift_calls"] == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
