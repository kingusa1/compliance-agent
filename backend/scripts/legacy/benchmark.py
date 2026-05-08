"""
Benchmark 3 checkpoint analysis approaches against ground truth.

Usage:
    python3 benchmark.py                    # test all 3 approaches on first available call
    python3 benchmark.py --call-id e9a28d20 # test specific call (prefix match)
    python3 benchmark.py --all              # test all completed calls
    python3 benchmark.py --approach 1       # test only approach 1
    python3 benchmark.py --provider openrouter  # use specific provider

Results saved to: benchmark_results.csv
"""

import argparse
import asyncio
import csv
import json
import os
import sys
import time
from datetime import datetime

# Add parent to path so we can import app modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.config import settings
from app.analysis import _call_llm
from app.verification import fuzzy_match


# ─── Prompts ────────────────────────────────────────────────────────────────

EXTRACT_PROMPT = """You are analyzing a compliance call transcript. Extract the exact quotes that correspond to each checkpoint listed below.

CHECKPOINTS TO FIND:
{checkpoint_list}

TRANSCRIPT:
{transcript}

For each checkpoint, find the EXACT quote from the transcript that covers it. Do not paraphrase — copy the exact words.

Return ONLY valid JSON — a list of objects:
[
  {{
    "checkpoint": "checkpoint name",
    "quote": "exact quote from transcript, or null if not found",
    "found": true or false
  }}
]"""

JUDGE_PROMPT = """You are judging whether a transcript quote satisfies a compliance checkpoint.

CHECKPOINT: {name}
STRICTNESS: {strictness}
REQUIRED: {required}
KEY PHRASES: {key_phrases}

QUOTE FROM TRANSCRIPT:
{quote}

Does this quote satisfy the checkpoint?
- "pass" if the quote covers what the checkpoint requires
- "partial" if the quote partially covers it but something is missing
- "fail" if the quote does not cover it or is null

Return ONLY valid JSON:
{{
  "status": "pass" or "partial" or "fail",
  "evidence": "the quote you evaluated",
  "notes": "what was missing (for partial/fail), null for pass"
}}"""

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

SINGLE_PROMPT = """You are a compliance auditor. Check ALL checkpoints against the transcript.

MODE: {mode}
STRICTNESS per checkpoint:
- verbatim: Agent must use near-exact wording
- mandatory: Agent must convey the meaning
- customer_yes: Both agent statement AND customer confirmation required

CHECKPOINTS:
{checkpoints_text}

TRANSCRIPT:
{transcript}

For EACH checkpoint:
- If FOUND: status "pass", quote the EXACT words
- If PARTIALLY FOUND: status "partial", quote what was said, explain what's missing
- If NOT FOUND: status "fail", evidence must be "NOT FOUND IN TRANSCRIPT"
- NEVER invent or paraphrase quotes

Return ONLY valid JSON:
{{
  "checkpoints": [
    {{
      "name": "checkpoint name",
      "status": "pass" or "partial" or "fail",
      "evidence": "exact quote or NOT FOUND IN TRANSCRIPT",
      "notes": "what was missing (for partial/fail), null for pass"
    }}
  ]
}}"""


def strip_fences(content: str) -> str:
    if content.startswith("```"):
        content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return content


# ─── Approach 1: Extract-Then-Judge ─────────────────────────────────────────

async def approach_1(transcript: str, checkpoints: list[dict], mode: str) -> dict:
    """Two-pass: extract quotes first, then judge each."""
    start = time.time()
    token_estimate = 0

    # Pass 1: Extract
    checkpoint_list = "\n".join(
        f"- {cp['name']}" for cp in checkpoints
    )
    extract_prompt = EXTRACT_PROMPT.format(
        checkpoint_list=checkpoint_list,
        transcript=transcript,
    )
    token_estimate += len(extract_prompt.split()) * 1.3  # rough estimate

    print(f"  [P1] Extracting quotes for {len(checkpoints)} checkpoints...")
    raw = await _call_llm(extract_prompt, timeout=60.0)
    raw = strip_fences(raw)
    extractions = json.loads(raw)
    token_estimate += len(raw.split()) * 1.3

    extract_time = time.time() - start
    print(f"  [P1] Done in {extract_time:.1f}s — {len(extractions)} extractions")

    # Build lookup
    quote_map = {}
    for ext in extractions:
        quote_map[ext["checkpoint"].lower().strip()] = ext.get("quote")

    # Pass 2: Judge each checkpoint
    results = []
    judge_start = time.time()

    async def judge_one(cp, idx):
        name = cp["name"]
        quote = quote_map.get(name.lower().strip())
        if not quote:
            # Try fuzzy name matching
            for key, val in quote_map.items():
                if key in name.lower() or name.lower() in key:
                    quote = val
                    break

        if not quote:
            return {
                "name": name,
                "status": "fail",
                "evidence": "NOT FOUND IN TRANSCRIPT",
                "notes": "No matching quote extracted in Pass 1",
                "verified": False,
                "similarity": 0,
            }

        prompt = JUDGE_PROMPT.format(
            name=name,
            strictness=cp.get("strictness", "mandatory"),
            required=cp.get("required", ""),
            key_phrases=", ".join(cp.get("key_phrases", [])),
            quote=quote,
        )
        nonlocal token_estimate
        token_estimate += len(prompt.split()) * 1.3

        raw = await _call_llm(prompt, timeout=30.0)
        raw = strip_fences(raw)
        parsed = json.loads(raw)
        token_estimate += len(raw.split()) * 1.3

        # Verify quote
        match = fuzzy_match(transcript, quote)

        status = parsed.get("status", "fail")
        if status in ("pass", "partial") and not match["verified"]:
            status = "unverified"

        return {
            "name": name,
            "status": status,
            "evidence": quote,
            "notes": parsed.get("notes"),
            "verified": match["verified"],
            "similarity": match["similarity"],
        }

    print(f"  [P2] Judging {len(checkpoints)} checkpoints in parallel...")
    tasks = [judge_one(cp, i) for i, cp in enumerate(checkpoints)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Handle exceptions
    final = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            final.append({
                "name": checkpoints[i]["name"],
                "status": "error",
                "evidence": str(r),
                "notes": "Error in judge pass",
                "verified": False,
                "similarity": 0,
            })
        else:
            final.append(r)

    total_time = time.time() - start
    judge_time = time.time() - judge_start
    print(f"  [P2] Done in {judge_time:.1f}s — total: {total_time:.1f}s")

    return {
        "approach": "extract-then-judge",
        "results": final,
        "time_seconds": round(total_time, 1),
        "token_estimate": int(token_estimate),
        "llm_calls": 1 + len(checkpoints),
    }


# ─── Approach 2: Batched ───────────────────────────────────────────────────

async def approach_2(transcript: str, checkpoints: list[dict], mode: str, batch_size: int = 6) -> dict:
    """Send checkpoints in batches of N with the transcript."""
    start = time.time()
    token_estimate = 0

    batches = [checkpoints[i:i+batch_size] for i in range(0, len(checkpoints), batch_size)]
    print(f"  Batching {len(checkpoints)} checkpoints into {len(batches)} groups of ~{batch_size}...")

    all_results = []

    async def run_batch(batch, batch_num):
        nonlocal token_estimate
        checkpoints_text = ""
        for cp in batch:
            checkpoints_text += f"\nCHECKPOINT: {cp['name']}\n"
            checkpoints_text += f"  Required: {cp.get('required', '')}\n"
            checkpoints_text += f"  Strictness: {cp.get('strictness', 'mandatory')}\n"
            checkpoints_text += f"  Key phrases: {', '.join(cp.get('key_phrases', []))}\n"

        prompt = BATCH_PROMPT.format(
            checkpoints_text=checkpoints_text,
            transcript=transcript,
        )
        token_estimate += len(prompt.split()) * 1.3

        print(f"  [Batch {batch_num+1}/{len(batches)}] Sending {len(batch)} checkpoints...")
        raw = await _call_llm(prompt, timeout=60.0)
        raw = strip_fences(raw)
        results = json.loads(raw)
        token_estimate += len(raw.split()) * 1.3

        # Verify quotes
        verified_results = []
        for r in results:
            match = fuzzy_match(transcript, r.get("evidence", ""))
            status = r.get("status", "fail")
            if status in ("pass", "partial") and not match["verified"]:
                status = "unverified"
            verified_results.append({
                "name": r.get("name", ""),
                "status": status,
                "evidence": r.get("evidence", ""),
                "notes": r.get("notes"),
                "verified": match["verified"],
                "similarity": match["similarity"],
            })
        return verified_results

    tasks = [run_batch(batch, i) for i, batch in enumerate(batches)]
    batch_results = await asyncio.gather(*tasks, return_exceptions=True)

    for i, br in enumerate(batch_results):
        if isinstance(br, Exception):
            for cp in batches[i]:
                all_results.append({
                    "name": cp["name"],
                    "status": "error",
                    "evidence": str(br),
                    "notes": "Batch error",
                    "verified": False,
                    "similarity": 0,
                })
        else:
            all_results.extend(br)

    total_time = time.time() - start
    print(f"  Done in {total_time:.1f}s")

    return {
        "approach": f"batched-{batch_size}",
        "results": all_results,
        "time_seconds": round(total_time, 1),
        "token_estimate": int(token_estimate),
        "llm_calls": len(batches),
    }


# ─── Approach 3: Single Prompt ──────────────────────────────────────────────

async def approach_3(transcript: str, checkpoints: list[dict], mode: str) -> dict:
    """One mega-prompt with all checkpoints."""
    start = time.time()
    token_estimate = 0

    checkpoints_text = ""
    for cp in checkpoints:
        checkpoints_text += f"\nCHECKPOINT {cp.get('section', '')}: {cp['name']}\n"
        checkpoints_text += f"  Required: {cp.get('required', '')}\n"
        checkpoints_text += f"  Strictness: {cp.get('strictness', 'mandatory')}\n"
        checkpoints_text += f"  Key phrases: {', '.join(cp.get('key_phrases', []))}\n"

    prompt = SINGLE_PROMPT.format(
        mode=mode,
        checkpoints_text=checkpoints_text,
        transcript=transcript,
    )
    token_estimate += len(prompt.split()) * 1.3

    print(f"  Sending all {len(checkpoints)} checkpoints in one call...")
    raw = await _call_llm(prompt, timeout=90.0)
    raw = strip_fences(raw)
    parsed = json.loads(raw)
    token_estimate += len(raw.split()) * 1.3

    raw_results = parsed.get("checkpoints", parsed) if isinstance(parsed, dict) else parsed

    # Verify quotes
    results = []
    for r in raw_results:
        match = fuzzy_match(transcript, r.get("evidence", ""))
        status = r.get("status", "fail")
        if status in ("pass", "partial") and not match["verified"]:
            status = "unverified"
        results.append({
            "name": r.get("name", ""),
            "status": status,
            "evidence": r.get("evidence", ""),
            "notes": r.get("notes"),
            "verified": match["verified"],
            "similarity": match["similarity"],
        })

    total_time = time.time() - start
    print(f"  Done in {total_time:.1f}s")

    return {
        "approach": "single-prompt",
        "results": results,
        "time_seconds": round(total_time, 1),
        "token_estimate": int(token_estimate),
        "llm_calls": 1,
    }


# ─── Accuracy Comparison ───────────────────────────────────────────────────

def compare_accuracy(ground_truth: list[dict], test_results: list[dict]) -> dict:
    """Compare test results against ground truth checkpoint results."""
    gt_map = {r["name"].lower().strip(): r["status"] for r in ground_truth}

    matches = 0
    mismatches = 0
    missing = 0
    details = []

    for tr in test_results:
        name = tr["name"].lower().strip()
        gt_status = gt_map.get(name)

        if gt_status is None:
            # Try fuzzy name matching
            for gt_name, gt_s in gt_map.items():
                if gt_name in name or name in gt_name:
                    gt_status = gt_s
                    break

        if gt_status is None:
            missing += 1
            details.append({"checkpoint": tr["name"], "gt": "MISSING", "test": tr["status"], "match": False})
        elif gt_status == tr["status"]:
            matches += 1
            details.append({"checkpoint": tr["name"], "gt": gt_status, "test": tr["status"], "match": True})
        else:
            mismatches += 1
            details.append({"checkpoint": tr["name"], "gt": gt_status, "test": tr["status"], "match": False})

    total = matches + mismatches + missing
    accuracy = (matches / total * 100) if total > 0 else 0

    return {
        "accuracy": round(accuracy, 1),
        "matches": matches,
        "mismatches": mismatches,
        "missing": missing,
        "total": total,
        "details": details,
    }


# ─── CSV Writer ─────────────────────────────────────────────────────────────

def write_csv(results: list[dict], filepath: str):
    """Append benchmark results to CSV."""
    file_exists = os.path.exists(filepath)
    fieldnames = [
        "timestamp", "call_id", "supplier", "checkpoints",
        "approach", "provider", "model",
        "time_seconds", "token_estimate", "llm_calls",
        "accuracy", "matches", "mismatches", "missing",
        "score_gt", "score_test",
    ]

    with open(filepath, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        for r in results:
            writer.writerow(r)

    print(f"\n  Results appended to {filepath}")


# ─── Main ──────────────────────────────────────────────────────────────────

async def run_benchmark(call_id: str, approaches: list[int], provider: str | None = None):
    """Run benchmark for a specific call."""
    import sqlite3

    if provider:
        settings.active_provider = provider
        print(f"Using provider: {provider} ({getattr(settings, f'{provider}_model', 'default')})")

    db = sqlite3.connect("compliance.db")
    db.row_factory = sqlite3.Row

    # Find the call (prefix match)
    call = db.execute(
        "SELECT * FROM calls WHERE id LIKE ? AND status='completed' AND checkpoint_results IS NOT NULL",
        (f"{call_id}%",)
    ).fetchone()

    if not call:
        print(f"No completed call found matching '{call_id}'")
        return

    call_id_full = call["id"]
    transcript = call["transcript"]
    supplier = call["detected_supplier"]
    gt_score = call["score"]
    gt_results = json.loads(call["checkpoint_results"])

    # Load script checkpoints
    script = db.execute("SELECT * FROM scripts WHERE id=?", (call["script_id"],)).fetchone()
    if not script:
        print(f"No script found for call {call_id_full[:8]}")
        return

    checkpoints = json.loads(script["checkpoints"])
    mode = script["mode"]

    print(f"\n{'='*70}")
    print(f"BENCHMARK: {supplier} — {script['script_name']}")
    print(f"Call: {call_id_full[:8]}... | {len(checkpoints)} checkpoints | Ground truth: {gt_score}")
    print(f"Provider: {settings.active_provider} | Model: {getattr(settings, f'{settings.active_provider}_model', 'N/A')}")
    print(f"{'='*70}")

    csv_rows = []

    # Approach 1: Extract-Then-Judge
    if 1 in approaches:
        print(f"\n--- Approach 1: Extract-Then-Judge ---")
        try:
            result = await approach_1(transcript, checkpoints, mode)
            acc = compare_accuracy(gt_results, result["results"])
            passed = sum(1 for r in result["results"] if r["status"] == "pass")
            total_non_error = sum(1 for r in result["results"] if r["status"] != "error")
            test_score = f"{passed}/{total_non_error}"
            print(f"  Score: {test_score} (GT: {gt_score})")
            print(f"  Accuracy: {acc['accuracy']}% ({acc['matches']}/{acc['total']} match GT)")
            print(f"  Time: {result['time_seconds']}s | Tokens: ~{result['token_estimate']} | Calls: {result['llm_calls']}")

            # Show mismatches
            for d in acc["details"]:
                if not d["match"]:
                    print(f"    MISMATCH: {d['checkpoint'][:40]}... GT={d['gt']} TEST={d['test']}")

            csv_rows.append({
                "timestamp": datetime.now().isoformat(),
                "call_id": call_id_full[:8],
                "supplier": supplier,
                "checkpoints": len(checkpoints),
                "approach": "extract-then-judge",
                "provider": settings.active_provider,
                "model": getattr(settings, f"{settings.active_provider}_model", ""),
                "time_seconds": result["time_seconds"],
                "token_estimate": result["token_estimate"],
                "llm_calls": result["llm_calls"],
                "accuracy": acc["accuracy"],
                "matches": acc["matches"],
                "mismatches": acc["mismatches"],
                "missing": acc["missing"],
                "score_gt": gt_score,
                "score_test": test_score,
            })
        except Exception as e:
            print(f"  FAILED: {e}")

    # Approach 2: Batched
    if 2 in approaches:
        print(f"\n--- Approach 2: Batched (6 per batch) ---")
        try:
            result = await approach_2(transcript, checkpoints, mode, batch_size=6)
            acc = compare_accuracy(gt_results, result["results"])
            passed = sum(1 for r in result["results"] if r["status"] == "pass")
            total_non_error = sum(1 for r in result["results"] if r["status"] != "error")
            test_score = f"{passed}/{total_non_error}"
            print(f"  Score: {test_score} (GT: {gt_score})")
            print(f"  Accuracy: {acc['accuracy']}% ({acc['matches']}/{acc['total']} match GT)")
            print(f"  Time: {result['time_seconds']}s | Tokens: ~{result['token_estimate']} | Calls: {result['llm_calls']}")

            for d in acc["details"]:
                if not d["match"]:
                    print(f"    MISMATCH: {d['checkpoint'][:40]}... GT={d['gt']} TEST={d['test']}")

            csv_rows.append({
                "timestamp": datetime.now().isoformat(),
                "call_id": call_id_full[:8],
                "supplier": supplier,
                "checkpoints": len(checkpoints),
                "approach": f"batched-6",
                "provider": settings.active_provider,
                "model": getattr(settings, f"{settings.active_provider}_model", ""),
                "time_seconds": result["time_seconds"],
                "token_estimate": result["token_estimate"],
                "llm_calls": result["llm_calls"],
                "accuracy": acc["accuracy"],
                "matches": acc["matches"],
                "mismatches": acc["mismatches"],
                "missing": acc["missing"],
                "score_gt": gt_score,
                "score_test": test_score,
            })
        except Exception as e:
            print(f"  FAILED: {e}")

    # Approach 3: Single Prompt
    if 3 in approaches:
        print(f"\n--- Approach 3: Single Prompt ---")
        try:
            result = await approach_3(transcript, checkpoints, mode)
            acc = compare_accuracy(gt_results, result["results"])
            passed = sum(1 for r in result["results"] if r["status"] == "pass")
            total_non_error = sum(1 for r in result["results"] if r["status"] != "error")
            test_score = f"{passed}/{total_non_error}"
            print(f"  Score: {test_score} (GT: {gt_score})")
            print(f"  Accuracy: {acc['accuracy']}% ({acc['matches']}/{acc['total']} match GT)")
            print(f"  Time: {result['time_seconds']}s | Tokens: ~{result['token_estimate']} | Calls: {result['llm_calls']}")

            for d in acc["details"]:
                if not d["match"]:
                    print(f"    MISMATCH: {d['checkpoint'][:40]}... GT={d['gt']} TEST={d['test']}")

            csv_rows.append({
                "timestamp": datetime.now().isoformat(),
                "call_id": call_id_full[:8],
                "supplier": supplier,
                "checkpoints": len(checkpoints),
                "approach": "single-prompt",
                "provider": settings.active_provider,
                "model": getattr(settings, f"{settings.active_provider}_model", ""),
                "time_seconds": result["time_seconds"],
                "token_estimate": result["token_estimate"],
                "llm_calls": result["llm_calls"],
                "accuracy": acc["accuracy"],
                "matches": acc["matches"],
                "mismatches": acc["mismatches"],
                "missing": acc["missing"],
                "score_gt": gt_score,
                "score_test": test_score,
            })
        except Exception as e:
            print(f"  FAILED: {e}")

    # Save to CSV
    if csv_rows:
        write_csv(csv_rows, "benchmark_results.csv")

    db.close()


def main():
    parser = argparse.ArgumentParser(description="Benchmark checkpoint analysis approaches")
    parser.add_argument("--call-id", default=None, help="Call ID prefix to test (default: first available)")
    parser.add_argument("--all", action="store_true", help="Test all completed calls")
    parser.add_argument("--approach", type=int, nargs="+", default=[1, 2, 3], help="Which approaches to test (1, 2, 3)")
    parser.add_argument("--provider", default=None, help="LLM provider to use (openrouter, gemini, anthropic, openai)")
    args = parser.parse_args()

    if args.all:
        import sqlite3
        db = sqlite3.connect("compliance.db")
        db.row_factory = sqlite3.Row
        calls = db.execute(
            "SELECT id FROM calls WHERE status='completed' AND checkpoint_results IS NOT NULL"
        ).fetchall()
        db.close()
        for call in calls:
            asyncio.run(run_benchmark(call["id"], args.approach, args.provider))
    elif args.call_id:
        asyncio.run(run_benchmark(args.call_id, args.approach, args.provider))
    else:
        # First available
        import sqlite3
        db = sqlite3.connect("compliance.db")
        db.row_factory = sqlite3.Row
        call = db.execute(
            "SELECT id FROM calls WHERE status='completed' AND checkpoint_results IS NOT NULL LIMIT 1"
        ).fetchone()
        db.close()
        if call:
            asyncio.run(run_benchmark(call["id"], args.approach, args.provider))
        else:
            print("No completed calls found in database")


if __name__ == "__main__":
    main()
