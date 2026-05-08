"""
Phase 2 Step 2.0: Benchmark audio-native models vs text-based baseline.

Sends RAW AUDIO + checkpoint definitions directly to audio-capable models.
No intermediate transcript — the model hears the call and judges compliance.

Models tested:
  - google/gemini-2.5-flash (audio input via OpenRouter)
  - google/gemini-2.5-pro (audio input via OpenRouter)
  - openai/gpt-audio-mini (audio input via OpenRouter)
  - openai/gpt-audio (audio input via OpenRouter)
  - BASELINE: Deepgram transcript + Sonnet batched (text-only)

Usage:
    python3 benchmark_audio_native.py
"""

import asyncio
import base64
import csv
import json
import os
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import httpx

os.environ.setdefault("ACTIVE_PROVIDER", "openrouter")
from importlib import reload
import app.config
reload(app.config)
from app.config import settings
settings.active_provider = "openrouter"

from app.analysis import _call_llm

OPENROUTER_KEY = settings.openrouter_api_key

# Test calls
TEST_CALLS = ["e9a28d20", "42111d0c", "9adbb141"]

# Audio-native models
AUDIO_MODELS = [
    "google/gemini-2.5-flash",
    "google/gemini-2.5-pro",
    "openai/gpt-audio-mini",
    "openai/gpt-audio",
]

AUDIO_PROMPT = """You are a compliance auditor. Listen to this energy sales call and check each checkpoint below.

CHECKPOINTS TO EVALUATE:
{checkpoints_text}

DEEPGRAM INTELLIGENCE (metadata about this call):
- Topics detected: {topics}
- Sentiment: {sentiment}
- Summary: {summary}

For EACH checkpoint:
- If the agent CLEARLY covered this checkpoint in the call: status "pass", quote the EXACT words you heard
- If the agent PARTIALLY covered it (said something related but incomplete): status "partial", quote what was said, explain what's missing
- If the agent did NOT cover this checkpoint at all: status "fail", evidence must be "NOT FOUND IN CALL"
- For "customer_yes" strictness: the customer must also confirm (yes, yeah, okay, mmhmm counts)

Return ONLY valid JSON array:
[
  {{
    "name": "checkpoint name",
    "status": "pass" or "partial" or "fail",
    "evidence": "exact quote heard from the call, or NOT FOUND IN CALL",
    "notes": "what was missing (for partial/fail), null for pass"
  }}
]"""

TEXT_BATCH_PROMPT = """You are a compliance auditor. Check the following checkpoints against the transcript.

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


def format_checkpoints(cps: list[dict]) -> str:
    text = ""
    for cp in cps:
        text += f"\nCHECKPOINT: {cp['name']}\n"
        text += f"  Required: {cp.get('required', '')}\n"
        text += f"  Strictness: {cp.get('strictness', 'mandatory')}\n"
        text += f"  Key phrases: {', '.join(cp.get('key_phrases', []))}\n"
    return text


def load_deepgram_intelligence(short_id: str) -> dict:
    """Load Deepgram intelligence metadata."""
    path = f"transcripts/deepgram_full/{short_id}.json"
    if not os.path.exists(path):
        return {"topics": "N/A", "sentiment": "N/A", "summary": "N/A"}

    raw = json.load(open(path))
    results = raw.get("results", {})

    # Topics
    topics = results.get("topics", {}).get("segments", [])
    topic_names = set()
    for seg in topics:
        for t in seg.get("topics", []):
            topic_names.add(t.get("topic", ""))
    topics_str = ", ".join(list(topic_names)[:10]) if topic_names else "N/A"

    # Sentiment
    sentiments = results.get("sentiments", {})
    avg = sentiments.get("average", {})
    sentiment_str = f"Overall: {avg}" if avg else "N/A"

    # Summary
    summary = results.get("summary", {})
    summary_str = summary.get("short", summary.get("text", "N/A"))

    return {"topics": topics_str, "sentiment": sentiment_str, "summary": summary_str[:200]}


async def run_audio_model(model: str, audio_path: str, checkpoints: list[dict], dg_intel: dict) -> dict:
    """Send raw audio + checkpoints to an audio-native model."""
    with open(audio_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    ext = os.path.splitext(audio_path)[1].lstrip(".")
    cp_text = format_checkpoints(checkpoints)
    prompt = AUDIO_PROMPT.format(
        checkpoints_text=cp_text,
        topics=dg_intel["topics"],
        sentiment=dg_intel["sentiment"],
        summary=dg_intel["summary"],
    )

    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            r = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "input_audio", "input_audio": {"data": b64, "format": ext or "mp3"}},
                        ],
                    }],
                    "temperature": 0,
                    "max_tokens": 16384,
                },
            )
            r.raise_for_status()

        elapsed = time.time() - t0
        data = r.json()
        content = data["choices"][0]["message"]["content"]
        cost = data.get("usage", {}).get("cost", 0)

        # Parse JSON
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        results = json.loads(content)
        return {
            "results": results,
            "time": round(elapsed, 1),
            "cost": cost,
            "error": None,
        }

    except Exception as e:
        elapsed = time.time() - t0
        return {
            "results": [],
            "time": round(elapsed, 1),
            "cost": 0,
            "error": str(e)[:200],
        }


async def run_text_baseline(transcript: str, checkpoints: list[dict]) -> dict:
    """Run text-based baseline: Deepgram transcript + Sonnet batched."""
    settings.openrouter_model = "anthropic/claude-sonnet-4-6"
    batches = [checkpoints[i:i+6] for i in range(0, len(checkpoints), 6)]
    all_results = []

    t0 = time.time()
    for batch in batches:
        cp_text = format_checkpoints(batch)
        prompt = TEXT_BATCH_PROMPT.format(checkpoints_text=cp_text, transcript=transcript)
        try:
            raw = await _call_llm(prompt, timeout=90.0)
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            all_results.extend(json.loads(raw))
        except Exception as e:
            for cp in batch:
                all_results.append({"name": cp["name"], "status": "error", "evidence": str(e)})

    elapsed = time.time() - t0
    return {"results": all_results, "time": round(elapsed, 1), "cost": 0, "error": None}


def score_against_gt(results: list[dict], gt_consensus: list[dict]) -> dict:
    """Compare results against consensus ground truth."""
    gt_map = {}
    for c in gt_consensus:
        gt_map[c["name"].lower().strip()] = c["status"]

    matches = mismatches = 0
    false_fails = false_passes = partial_disag = 0
    details = []

    for r in results:
        rn = r.get("name", "").lower().strip()
        gs = gt_map.get(rn)
        if not gs:
            for k, v in gt_map.items():
                if k in rn or rn in k:
                    gs = v
                    break
        if not gs:
            continue

        rs = r.get("status", "unknown")
        if gs == rs:
            matches += 1
        else:
            mismatches += 1
            if gs == "pass" and rs in ("fail", "unverified"):
                false_fails += 1
            elif gs in ("fail",) and rs == "pass":
                false_passes += 1
            else:
                partial_disag += 1
            details.append(f"    {r.get('name','')[:42]:42s} GT={gs:8s} GOT={rs}")

    total = matches + mismatches
    acc = matches / total * 100 if total else 0
    passed = sum(1 for r in results if r.get("status") == "pass")

    return {
        "accuracy": round(acc, 1),
        "matches": matches,
        "mismatches": mismatches,
        "total": total,
        "passed": passed,
        "false_fails": false_fails,
        "false_passes": false_passes,
        "partial_disagreements": partial_disag,
        "details": details,
    }


async def main():
    db = sqlite3.connect("compliance.db")
    db.row_factory = sqlite3.Row

    os.makedirs("benchmark/audio_native", exist_ok=True)

    print(f"\n{'='*80}")
    print(f"AUDIO-NATIVE MODEL BENCHMARK")
    print(f"4 audio models + 1 text baseline × 3 calls = 15 tests")
    print(f"{'='*80}\n")

    all_csv_rows = []

    for short_id in TEST_CALLS:
        call = db.execute(f"SELECT * FROM calls WHERE id LIKE '{short_id}%'").fetchone()
        if not call:
            print(f"SKIP {short_id} — not found")
            continue

        script = db.execute("SELECT * FROM scripts WHERE id=?", (call["script_id"],)).fetchone()
        if not script:
            print(f"SKIP {short_id} — no script")
            continue

        checkpoints = json.loads(script["checkpoints"])
        supplier = call["detected_supplier"] or "Unknown"
        audio_path = call["file_path"]

        gt_path = f"benchmark/ground_truth/{short_id}_consensus.json"
        if not os.path.exists(gt_path):
            print(f"SKIP {short_id} — no GT")
            continue

        gt = json.load(open(gt_path))
        gt_consensus = gt["consensus"]
        dg_intel = load_deepgram_intelligence(short_id)

        print(f"{'─'*80}")
        print(f"CALL: {short_id} | {supplier} | {script['script_name']} | {len(checkpoints)} checkpoints")
        print(f"GT score: {gt['stats']['score']}")
        print(f"{'─'*80}")

        # Run text baseline first
        print(f"\n  [BASELINE] Deepgram transcript + Sonnet batched...")
        baseline = await run_text_baseline(call["transcript"], checkpoints)
        if baseline["error"]:
            print(f"    ERROR: {baseline['error']}")
        else:
            score = score_against_gt(baseline["results"], gt_consensus)
            print(f"    Accuracy: {score['accuracy']}% | Score: {score['passed']}/{score['total']} | Time: {baseline['time']}s | FF:{score['false_fails']} FP:{score['false_passes']} PD:{score['partial_disagreements']}")
            for d in score["details"][:5]:
                print(d)

            all_csv_rows.append({
                "call_id": short_id, "supplier": supplier, "model": "BASELINE:sonnet+deepgram",
                "accuracy": score["accuracy"], "passed": score["passed"], "total": score["total"],
                "false_fails": score["false_fails"], "false_passes": score["false_passes"],
                "time_s": baseline["time"], "cost": 0, "error": "",
            })

        # Run each audio model
        for model in AUDIO_MODELS:
            print(f"\n  [{model}] Sending raw audio + checkpoints...")
            result = await run_audio_model(model, audio_path, checkpoints, dg_intel)

            if result["error"]:
                print(f"    ERROR: {result['error'][:100]}")
                all_csv_rows.append({
                    "call_id": short_id, "supplier": supplier, "model": model,
                    "accuracy": 0, "passed": 0, "total": 0,
                    "false_fails": 0, "false_passes": 0,
                    "time_s": result["time"], "cost": result["cost"], "error": result["error"][:100],
                })
            else:
                score = score_against_gt(result["results"], gt_consensus)
                print(f"    Accuracy: {score['accuracy']}% | Score: {score['passed']}/{score['total']} | Time: {result['time']}s | Cost: ${result['cost']:.4f} | FF:{score['false_fails']} FP:{score['false_passes']} PD:{score['partial_disagreements']}")
                for d in score["details"][:5]:
                    print(d)

                all_csv_rows.append({
                    "call_id": short_id, "supplier": supplier, "model": model,
                    "accuracy": score["accuracy"], "passed": score["passed"], "total": score["total"],
                    "false_fails": score["false_fails"], "false_passes": score["false_passes"],
                    "time_s": result["time"], "cost": result["cost"], "error": "",
                })

            # Save raw results
            safe_model = model.replace("/", "_")
            with open(f"benchmark/audio_native/{short_id}_{safe_model}.json", "w") as f:
                json.dump(result, f, indent=2)

            await asyncio.sleep(2)  # rate limit spacing

    # Save CSV
    csv_path = "benchmark/audio_native_results.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "call_id", "supplier", "model", "accuracy", "passed", "total",
            "false_fails", "false_passes", "time_s", "cost", "error",
        ])
        writer.writeheader()
        writer.writerows(all_csv_rows)

    # Print summary
    print(f"\n\n{'='*80}")
    print(f"SUMMARY — Average accuracy across all 3 calls")
    print(f"{'='*80}")

    model_stats = {}
    for row in all_csv_rows:
        m = row["model"]
        if m not in model_stats:
            model_stats[m] = {"accs": [], "costs": [], "times": [], "errors": 0}
        if row["error"]:
            model_stats[m]["errors"] += 1
        else:
            model_stats[m]["accs"].append(row["accuracy"])
            model_stats[m]["costs"].append(row["cost"])
            model_stats[m]["times"].append(row["time_s"])

    print(f"\n{'Model':40s} {'Avg Acc':>8s} {'Avg Time':>9s} {'Avg Cost':>9s} {'Errors':>7s}")
    print("-" * 78)
    for m, s in sorted(model_stats.items(), key=lambda x: -(sum(x[1]["accs"])/len(x[1]["accs"]) if x[1]["accs"] else 0)):
        avg_acc = sum(s["accs"]) / len(s["accs"]) if s["accs"] else 0
        avg_time = sum(s["times"]) / len(s["times"]) if s["times"] else 0
        avg_cost = sum(s["costs"]) / len(s["costs"]) if s["costs"] else 0
        print(f"{m:40s} {avg_acc:>7.1f}% {avg_time:>8.1f}s ${avg_cost:>7.4f} {s['errors']:>7d}")

    print(f"\nResults saved to: {csv_path}")
    print(f"Raw results in: benchmark/audio_native/")

    db.close()


if __name__ == "__main__":
    asyncio.run(main())
