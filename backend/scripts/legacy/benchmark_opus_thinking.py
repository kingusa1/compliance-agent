"""
One-off benchmark: Claude Opus 4.7 with extended thinking, batch-6, on call e9a28d20.

Usage: python3 backend/benchmark_opus_thinking.py
"""
import asyncio
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

os.environ.setdefault("ACTIVE_PROVIDER", "openrouter")
from app.config import settings

import httpx


CALL_ID_PREFIX = "e9a28d20"
MODEL = "anthropic/claude-opus-4.7"
BATCH_SIZE = 6
REASONING_EFFORT = "high"
MAX_TOKENS = 16000
TIMEOUT = 240.0

# Per-token pricing for Claude Opus 4.7 (Anthropic list price via OpenRouter).
# Update these two constants if OpenRouter quotes differ at runtime.
PRICE_INPUT_PER_M = 15.00   # $ per 1M input tokens
PRICE_OUTPUT_PER_M = 75.00  # $ per 1M output tokens (thinking tokens billed as output)


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


def fmt_checkpoints(cps):
    out = ""
    for cp in cps:
        out += f"\nCHECKPOINT: {cp['name']}\n"
        out += f"  Required: {cp.get('required', '')}\n"
        out += f"  Strictness: {cp.get('strictness', 'mandatory')}\n"
        kp = cp.get('key_phrases', [])
        if kp:
            out += f"  Key phrases: {', '.join(kp)}\n"
    return out


def strip_fences(content: str) -> str:
    if content.startswith("```"):
        content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return content


async def call_opus_thinking(prompt: str) -> tuple[str, dict]:
    """Call Opus 4.7 via OpenRouter with reasoning enabled. Returns (text, usage)."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://compliance-agent-poc-xi.vercel.app",
                "X-Title": "Compliance Agent Opus Thinking Bench",
            },
            json={
                "model": MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 1,   # Anthropic requires T=1 when thinking is on
                "max_tokens": MAX_TOKENS,
                "reasoning": {"max_tokens": 8000, "enabled": True},
                "include_reasoning": True,
            },
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
    data = resp.json()
    text = data["choices"][0]["message"]["content"].strip()
    text = strip_fences(text)
    usage = data.get("usage", {})
    return text, usage


def load_call_and_checkpoints():
    db = sqlite3.connect("backend/compliance.db")
    db.row_factory = sqlite3.Row
    call = db.execute(
        "SELECT * FROM calls WHERE id LIKE ? AND status='completed' LIMIT 1",
        (f"{CALL_ID_PREFIX}%",)
    ).fetchone()
    if not call:
        print(f"No call found for {CALL_ID_PREFIX}")
        sys.exit(1)
    script = db.execute("SELECT * FROM scripts WHERE id=?", (call["script_id"],)).fetchone()
    checkpoints = json.loads(script["checkpoints"])
    return dict(call), checkpoints


def load_consensus_gt():
    path = f"backend/benchmark/ground_truth/{CALL_ID_PREFIX}_consensus.json"
    return json.load(open(path))


def score_vs_consensus(predictions, consensus):
    """Mirror compare() from accuracy_benchmark.py — tracks false_pass / false_fail / partial_disag / unknown."""
    gt_map = {c["name"].lower().strip(): c for c in consensus}
    matches = mismatches = missing = 0
    false_fails = false_passes = partial_disag = unknown = 0
    details = []

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

        pr_status = pr.get("status", "unknown")
        gt_status = gt["status"]

        if pr_status == "error":
            unknown += 1
            details.append(("UNKNOWN", pr_name, gt_status, pr_status))
        elif pr_status == gt_status:
            matches += 1
        else:
            mismatches += 1
            if pr_status == "fail" and gt_status == "pass":
                false_fails += 1
                details.append(("FALSE_FAIL", pr_name, gt_status, pr_status))
            elif pr_status == "pass" and gt_status == "fail":
                false_passes += 1
                details.append(("FALSE_PASS", pr_name, gt_status, pr_status))
            elif "partial" in (pr_status, gt_status):
                partial_disag += 1
                details.append(("PARTIAL_DISAG", pr_name, gt_status, pr_status))
            else:
                details.append(("OTHER", pr_name, gt_status, pr_status))

    total = matches + mismatches + missing + unknown
    acc = (matches / total * 100) if total else 0
    return {
        "accuracy": round(acc, 1),
        "matches": matches,
        "mismatches": mismatches,
        "missing": missing,
        "unknown": unknown,
        "false_fails": false_fails,
        "false_passes": false_passes,
        "partial_disag": partial_disag,
        "total": total,
        "details": details,
    }


async def main():
    call, checkpoints = load_call_and_checkpoints()
    transcript = call["transcript"]
    print(f"\n{'=' * 72}")
    print(f"OPUS 4.7 + EXTENDED THINKING · batch-{BATCH_SIZE} benchmark")
    print(f"{'=' * 72}")
    print(f"Model:     {MODEL}  (reasoning.effort = {REASONING_EFFORT})")
    print(f"Call:      {call['id'][:8]}...")
    print(f"Filename:  {call['filename']}")
    print(f"Supplier:  {call['detected_supplier']}")
    print(f"Checkpoints: {len(checkpoints)}")
    print(f"{'=' * 72}\n")

    batches = [checkpoints[i:i + BATCH_SIZE] for i in range(0, len(checkpoints), BATCH_SIZE)]
    print(f"Running {len(batches)} batched requests ({BATCH_SIZE} checkpoints each)...\n")

    all_results = []
    total_tokens_in = 0
    total_tokens_out = 0
    total_reasoning_tokens = 0
    t0 = time.time()

    for i, batch in enumerate(batches, 1):
        cp_text = fmt_checkpoints(batch)
        prompt = PROMPT_TEMPLATE.format(checkpoints_text=cp_text, transcript=transcript)
        t_batch = time.time()
        try:
            raw, usage = await call_opus_thinking(prompt)
            in_t = usage.get("prompt_tokens", 0)
            out_t = usage.get("completion_tokens", 0)
            reasoning_t = usage.get("completion_tokens_details", {}).get("reasoning_tokens", 0) or 0
            total_tokens_in += in_t
            total_tokens_out += out_t
            total_reasoning_tokens += reasoning_t
            results = json.loads(raw)
            all_results.extend(results)
            print(f"  Batch {i}/{len(batches)}: {len(results)} verdicts · {time.time()-t_batch:.1f}s · in={in_t} out={out_t} (reasoning={reasoning_t})")
        except Exception as e:
            print(f"  Batch {i}/{len(batches)}: ERROR — {type(e).__name__}: {str(e)[:160]}")
            for cp in batch:
                all_results.append({"name": cp["name"], "status": "error", "evidence": str(e)[:100]})

    elapsed = time.time() - t0

    gt = load_consensus_gt()
    consensus = gt.get("consensus", gt) if isinstance(gt, dict) else gt
    score = score_vs_consensus(all_results, consensus)

    in_cost = total_tokens_in / 1_000_000 * PRICE_INPUT_PER_M
    out_cost = total_tokens_out / 1_000_000 * PRICE_OUTPUT_PER_M
    total_cost = in_cost + out_cost

    print(f"\n{'=' * 72}")
    print(f"RESULTS")
    print(f"{'=' * 72}")
    print(f"Accuracy:         {score['accuracy']}%  ({score['matches']}/{score['total']} match consensus)")
    print(f"False passes:     {score['false_passes']}")
    print(f"False fails:      {score['false_fails']}")
    print(f"Partial drift:    {score['partial_disag']}")
    print(f"Unknown/error:    {score['unknown']}")
    print(f"Missing (no GT):  {score['missing']}")
    print(f"Elapsed:          {elapsed:.1f}s wall-clock")
    print(f"Tokens:           {total_tokens_in} in + {total_tokens_out} out  (reasoning: {total_reasoning_tokens})")
    print(f"Cost:             ${total_cost:.4f}  (in ${in_cost:.4f} + out ${out_cost:.4f})")
    print(f"API calls:        {len(batches)}")
    print(f"{'=' * 72}\n")

    if score["details"]:
        print("Disagreements / issues:")
        for tag, name, gt_s, pr_s in score["details"][:20]:
            print(f"  [{tag}] {name}: consensus={gt_s} vs opus-thinking={pr_s}")
        print()

    out_path = f"backend/benchmark/opus_thinking_{CALL_ID_PREFIX}.json"
    with open(out_path, "w") as f:
        json.dump({
            "model": MODEL,
            "reasoning_effort": REASONING_EFFORT,
            "batch_size": BATCH_SIZE,
            "call_id": call["id"],
            "filename": call["filename"],
            "supplier": call["detected_supplier"],
            "num_checkpoints": len(checkpoints),
            "api_calls": len(batches),
            "elapsed_s": round(elapsed, 1),
            "tokens_in": total_tokens_in,
            "tokens_out": total_tokens_out,
            "reasoning_tokens": total_reasoning_tokens,
            "cost_usd": round(total_cost, 4),
            "score": score,
            "predictions": all_results,
        }, f, indent=2, default=str)
    print(f"Written: {out_path}\n")


if __name__ == "__main__":
    asyncio.run(main())
