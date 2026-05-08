"""
Run Claude Opus 4.7 on all 3 reference calls, 3 runs each, for a
statistically defensible 9-data-point sample. Writes a summary JSON
that the benchmark briefing doc pulls numbers from.

Usage: python3 backend/benchmark_opus_3call.py
"""
import asyncio
import json
import os
import sqlite3
import statistics
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

os.environ.setdefault("ACTIVE_PROVIDER", "openrouter")
from app.config import settings

import httpx


REFERENCE_CALLS = ["e9a28d20", "42111d0c", "9adbb141"]
RUNS_PER_CALL = 3
MODEL = "anthropic/claude-opus-4.7"
BATCH_SIZE = 6
MAX_TOKENS = 16000
TIMEOUT = 300.0

PRICE_INPUT_PER_M = 15.00
PRICE_OUTPUT_PER_M = 75.00


PROMPT_TEMPLATE = """You are a compliance auditor. Check the following checkpoints against the transcript.

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
  {{"name": "checkpoint name", "status": "pass" or "partial" or "fail", "evidence": "quote", "notes": "null for pass"}}
]"""


def fmt_cps(cps):
    out = ""
    for cp in cps:
        out += f"\nCHECKPOINT: {cp['name']}\n"
        out += f"  Required: {cp.get('required', '')}\n"
        out += f"  Strictness: {cp.get('strictness', 'mandatory')}\n"
        kp = cp.get("key_phrases", [])
        if kp:
            out += f"  Key phrases: {', '.join(kp)}\n"
    return out


def strip_fences(content: str) -> str:
    if content.startswith("```"):
        content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return content


async def call_opus(prompt: str) -> tuple[str, dict]:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://compliance-agent-poc-xi.vercel.app",
                "X-Title": "Compliance Agent 3-Call Opus Bench",
            },
            json={
                "model": MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 1,
                "max_tokens": MAX_TOKENS,
                "reasoning": {"max_tokens": 8000, "enabled": True},
                "include_reasoning": True,
            },
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
    d = resp.json()
    return strip_fences(d["choices"][0]["message"]["content"].strip()), d.get("usage", {})


def load_call(prefix: str):
    db = sqlite3.connect("backend/compliance.db")
    db.row_factory = sqlite3.Row
    call = db.execute(
        "SELECT * FROM calls WHERE id LIKE ? AND status='completed' LIMIT 1",
        (f"{prefix}%",),
    ).fetchone()
    if not call:
        raise SystemExit(f"No call found for {prefix}")
    script = db.execute("SELECT * FROM scripts WHERE id=?", (call["script_id"],)).fetchone()
    return dict(call), json.loads(script["checkpoints"])


def load_gt(prefix: str):
    path = f"backend/benchmark/ground_truth/{prefix}_consensus.json"
    data = json.load(open(path))
    return data.get("consensus", data) if isinstance(data, dict) else data


def score(predictions, consensus):
    gt_map = {c["name"].lower().strip(): c for c in consensus}
    matches = mismatches = missing = 0
    false_fails = false_passes = partial = unknown = 0
    for pr in predictions:
        pr_name = pr.get("name", "").lower().strip()
        gt = gt_map.get(pr_name)
        if not gt:
            for gn, gc in gt_map.items():
                if gn in pr_name or pr_name in gn:
                    gt = gc
                    break
        if not gt:
            missing += 1
            continue
        pr_s = pr.get("status", "unknown")
        gt_s = gt["status"]
        if pr_s == "error":
            unknown += 1
        elif pr_s == gt_s:
            matches += 1
        else:
            mismatches += 1
            if pr_s == "fail" and gt_s == "pass":
                false_fails += 1
            elif pr_s == "pass" and gt_s == "fail":
                false_passes += 1
            elif "partial" in (pr_s, gt_s):
                partial += 1
    total = matches + mismatches + missing + unknown
    return {
        "accuracy": round(matches / total * 100, 1) if total else 0,
        "matches": matches, "mismatches": mismatches, "missing": missing,
        "false_fails": false_fails, "false_passes": false_passes,
        "partial_disag": partial, "unknown": unknown, "total": total,
    }


async def run_once(call, checkpoints):
    batches = [checkpoints[i:i + BATCH_SIZE] for i in range(0, len(checkpoints), BATCH_SIZE)]
    results = []
    tok_in = tok_out = 0
    t0 = time.time()
    for batch in batches:
        prompt = PROMPT_TEMPLATE.format(
            checkpoints_text=fmt_cps(batch),
            transcript=call["transcript"],
        )
        try:
            raw, usage = await call_opus(prompt)
            tok_in += usage.get("prompt_tokens", 0)
            tok_out += usage.get("completion_tokens", 0)
            results.extend(json.loads(raw))
        except Exception as e:
            for cp in batch:
                results.append({"name": cp["name"], "status": "error", "evidence": str(e)[:120]})
    elapsed = time.time() - t0
    cost = tok_in / 1_000_000 * PRICE_INPUT_PER_M + tok_out / 1_000_000 * PRICE_OUTPUT_PER_M
    return results, elapsed, tok_in, tok_out, cost


async def main():
    all_runs = []  # list of dicts
    for prefix in REFERENCE_CALLS:
        call, checkpoints = load_call(prefix)
        consensus = load_gt(prefix)
        print(f"\n{'='*72}")
        print(f"Call {prefix}  {call['detected_supplier']:<18}  {len(checkpoints)} checkpoints")
        print(f"File: {call['filename']}")
        print(f"{'='*72}")
        call_runs = []
        for run_i in range(1, RUNS_PER_CALL + 1):
            preds, elapsed, tin, tout, cost = await run_once(call, checkpoints)
            s = score(preds, consensus)
            call_runs.append({
                "run": run_i, "accuracy": s["accuracy"], "time_s": round(elapsed, 1),
                "cost_usd": round(cost, 4), "tokens_in": tin, "tokens_out": tout,
                "false_passes": s["false_passes"], "false_fails": s["false_fails"],
                "partial_disag": s["partial_disag"], "unknown": s["unknown"],
                "details": {k: v for k, v in s.items() if k not in ("details",)},
            })
            print(f"  Run {run_i}: acc={s['accuracy']}%  false_pass={s['false_passes']}  "
                  f"false_fail={s['false_fails']}  partial={s['partial_disag']}  "
                  f"time={elapsed:.1f}s  cost=${cost:.4f}")
        all_runs.append({"call_id": prefix, "filename": call["filename"],
                         "supplier": call["detected_supplier"],
                         "checkpoints": len(checkpoints), "runs": call_runs})

    print(f"\n{'='*72}")
    print("PER-CALL MEANS")
    print(f"{'='*72}")
    per_call_means = {}
    for entry in all_runs:
        accs = [r["accuracy"] for r in entry["runs"]]
        costs = [r["cost_usd"] for r in entry["runs"]]
        times = [r["time_s"] for r in entry["runs"]]
        per_call_means[entry["call_id"]] = {
            "accuracy_mean": round(statistics.mean(accs), 2),
            "accuracy_min": min(accs), "accuracy_max": max(accs),
            "cost_mean": round(statistics.mean(costs), 4),
            "time_mean": round(statistics.mean(times), 1),
        }
        print(f"  {entry['call_id']}  {entry['supplier']:<18}  "
              f"acc {statistics.mean(accs):.2f}% (range {min(accs)}–{max(accs)})  "
              f"cost ${statistics.mean(costs):.4f}  time {statistics.mean(times):.1f}s")

    grand = [r["accuracy"] for e in all_runs for r in e["runs"]]
    grand_cost = [r["cost_usd"] for e in all_runs for r in e["runs"]]
    print(f"\nGRAND MEAN across 3 calls × {RUNS_PER_CALL} runs:  "
          f"acc {statistics.mean(grand):.2f}%  cost ${statistics.mean(grand_cost):.4f}")

    out_path = "backend/benchmark/opus_3call_summary.json"
    with open(out_path, "w") as f:
        json.dump({
            "model": MODEL, "batch_size": BATCH_SIZE, "runs_per_call": RUNS_PER_CALL,
            "calls": all_runs, "per_call_means": per_call_means,
            "grand_mean_accuracy": round(statistics.mean(grand), 2),
            "grand_mean_cost": round(statistics.mean(grand_cost), 4),
        }, f, indent=2, default=str)
    print(f"\nWritten: {out_path}\n")


if __name__ == "__main__":
    asyncio.run(main())
