"""
Generate multi-model consensus ground truth for all calls.

Runs 3 LLMs on each call's Gemini transcript, takes majority vote per checkpoint.

Usage:
    python3 generate_consensus_gt.py --all
    python3 generate_consensus_gt.py --all --skip-existing
    python3 generate_consensus_gt.py --call-id e9a28d20
"""

import argparse
import asyncio
import json
import os
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

os.environ.setdefault("ACTIVE_PROVIDER", "openrouter")

from importlib import reload
import app.config
reload(app.config)
from app.config import settings
settings.active_provider = "openrouter"

from app.analysis import _call_llm


MODELS = [
    "anthropic/claude-sonnet-4-6",
    "google/gemini-2.5-flash",
    "google/gemini-2.5-pro",
]

BATCH_PROMPT = """You are a compliance auditor. Check the following checkpoints against the transcript.

CHECKPOINTS:
{checkpoints_text}

TRANSCRIPT:
{transcript}

For EACH checkpoint:
- If FOUND: status "pass", quote the EXACT words from the transcript
- If PARTIALLY FOUND: status "partial", quote what was said, explain what's missing
- If NOT FOUND: status "fail", evidence must be "NOT FOUND IN TRANSCRIPT"

Return ONLY valid JSON array:
[
  {{
    "name": "checkpoint name",
    "status": "pass" or "partial" or "fail",
    "evidence": "exact quote or NOT FOUND IN TRANSCRIPT",
    "notes": "what was missing (for partial/fail), null for pass"
  }}
]"""


def build_prompt(cps, transcript):
    cp_text = ""
    for cp in cps:
        cp_text += f"\nCHECKPOINT: {cp['name']}\n"
        cp_text += f"  Required: {cp.get('required', '')}\n"
        cp_text += f"  Strictness: {cp.get('strictness', 'mandatory')}\n"
        cp_text += f"  Key phrases: {', '.join(cp.get('key_phrases', []))}\n"
    return BATCH_PROMPT.format(checkpoints_text=cp_text, transcript=transcript)


async def run_model(model_id, checkpoints, transcript):
    """Run one model on all checkpoints in batches of 6."""
    settings.openrouter_model = model_id
    batches = [checkpoints[i:i+6] for i in range(0, len(checkpoints), 6)]
    all_results = []

    for batch in batches:
        prompt = build_prompt(batch, transcript)
        try:
            raw = await _call_llm(prompt, timeout=90.0)
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            results = json.loads(raw)
            all_results.extend(results)
        except Exception as e:
            print(f"    {model_id} batch error: {e}")
            for cp in batch:
                all_results.append({"name": cp["name"], "status": "error", "evidence": str(e)})
        await asyncio.sleep(1)

    return all_results


def build_consensus(model_results: dict, cp_names: list) -> list:
    """Build majority-vote consensus from multiple model results."""
    models = list(model_results.keys())
    consensus = []

    for cp_name in cp_names:
        votes = {}
        for model in models:
            results = model_results[model]
            status = "missing"
            evidence = ""
            for r in results:
                rn = r["name"].lower().strip()
                cn = cp_name.lower().strip()
                if rn == cn or cn in rn or rn in cn:
                    status = r["status"]
                    evidence = r.get("evidence", "")
                    break
            if status not in ("missing", "error"):
                votes[model] = {"status": status, "evidence": evidence}

        vote_statuses = [v["status"] for v in votes.values()]
        vote_counts = Counter(vote_statuses)
        winner = vote_counts.most_common(1)[0] if vote_counts else ("unknown", 0)
        total_votes = len(vote_statuses)

        # Pick evidence from a model that voted with the majority
        best_evidence = ""
        for model, v in votes.items():
            if v["status"] == winner[0]:
                best_evidence = v["evidence"]
                break

        consensus.append({
            "name": cp_name,
            "status": winner[0],
            "agreement": winner[1],
            "total_votes": total_votes,
            "pct": round(winner[1] / total_votes * 100) if total_votes else 0,
            "votes": {m: v["status"] for m, v in votes.items()},
            "evidence": best_evidence,
        })

    return consensus


async def process_call(call_id: str, db: sqlite3.Connection, skip_existing: bool = False):
    """Generate consensus GT for a single call."""
    short_id = call_id[:8]
    out_path = f"benchmark/ground_truth/{short_id}_consensus.json"

    if skip_existing and os.path.exists(out_path):
        print(f"  SKIP {short_id} — already exists")
        return

    call = db.execute("SELECT * FROM calls WHERE id=? AND transcript IS NOT NULL", (call_id,)).fetchone()
    if not call:
        print(f"  SKIP {short_id} — no transcript")
        return

    if not call["script_id"]:
        print(f"  SKIP {short_id} — no script assigned")
        return

    script = db.execute("SELECT * FROM scripts WHERE id=?", (call["script_id"],)).fetchone()
    if not script:
        print(f"  SKIP {short_id} — script not found")
        return

    checkpoints = json.loads(script["checkpoints"])
    supplier = call["detected_supplier"] or "Unknown"

    # Use Gemini transcript if available, fallback to Deepgram
    gemini_path = f"transcripts/gemini/{short_id}.txt"
    if os.path.exists(gemini_path):
        transcript = open(gemini_path).read()
        transcript_source = "gemini-2.5-flash"
    else:
        transcript = call["transcript"]
        transcript_source = "deepgram-nova2"

    print(f"  {short_id} | {supplier} | {script['script_name']} | {len(checkpoints)} cp | transcript: {transcript_source}")

    # Run all 3 models
    model_results = {}
    total_cost = 0
    for model in MODELS:
        t0 = time.time()
        results = await run_model(model, checkpoints, transcript)
        elapsed = time.time() - t0
        errors = sum(1 for r in results if r["status"] == "error")
        passed = sum(1 for r in results if r["status"] == "pass")
        print(f"    {model}: {elapsed:.1f}s | {passed} pass, {errors} error")

        if errors > len(checkpoints) / 2:
            print(f"    SKIP model — too many errors")
            continue

        model_results[model] = results

    if len(model_results) < 2:
        print(f"    ABORT — need at least 2 working models")
        return

    # Build consensus
    cp_names = [cp["name"] for cp in checkpoints]
    consensus = build_consensus(model_results, cp_names)

    passed = sum(1 for c in consensus if c["status"] == "pass")
    unanimous = sum(1 for c in consensus if c["pct"] == 100)
    total = len(consensus)

    print(f"    CONSENSUS: {passed}/{total} pass | {unanimous}/{total} unanimous")

    # Save
    with open(out_path, "w") as f:
        json.dump({
            "call_id": call_id,
            "short_id": short_id,
            "supplier": supplier,
            "script_name": script["script_name"],
            "transcript_source": transcript_source,
            "models": list(model_results.keys()),
            "model_count": len(model_results),
            "timestamp": datetime.now().isoformat(),
            "consensus": consensus,
            "stats": {
                "score": f"{passed}/{total}",
                "passed": passed,
                "total": total,
                "unanimous": unanimous,
                "high_confidence": sum(1 for c in consensus if c["pct"] >= 80),
                "low_confidence": sum(1 for c in consensus if c["pct"] < 60),
            },
        }, f, indent=2)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--call-id", default=None)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--skip-existing", action="store_true")
    args = parser.parse_args()

    os.makedirs("benchmark/ground_truth", exist_ok=True)

    db = sqlite3.connect("compliance.db")
    db.row_factory = sqlite3.Row

    if args.call_id:
        calls = db.execute(
            "SELECT id FROM calls WHERE id LIKE ? AND transcript IS NOT NULL",
            (f"{args.call_id}%",),
        ).fetchall()
    elif args.all:
        calls = db.execute(
            "SELECT id FROM calls WHERE transcript IS NOT NULL AND script_id IS NOT NULL"
        ).fetchall()
    else:
        print("Usage: python3 generate_consensus_gt.py --all [--skip-existing]")
        return

    print(f"\n{'='*70}")
    print(f"GENERATING CONSENSUS GROUND TRUTH")
    print(f"Calls: {len(calls)} | Models: {len(MODELS)}")
    print(f"{'='*70}\n")

    for i, call in enumerate(calls):
        print(f"[{i+1}/{len(calls)}]")
        await process_call(call["id"], db, args.skip_existing)
        print()

    # Summary
    gt_files = [f for f in os.listdir("benchmark/ground_truth") if f.endswith("_consensus.json")]
    print(f"{'='*70}")
    print(f"DONE — {len(gt_files)} ground truth files in benchmark/ground_truth/")
    print(f"{'='*70}")

    db.close()


if __name__ == "__main__":
    asyncio.run(main())
