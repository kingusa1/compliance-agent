"""
Layer 2 Benchmark: 14 tests to optimize checkpoint judgment accuracy.

Uses AssemblyAI transcript + all intelligence data from AssemblyAI and Deepgram.
All tests measured against consensus ground truth.

Usage:
    python3 benchmark_layer2.py
    python3 benchmark_layer2.py --test 14  # run only test 14
"""

import asyncio
import csv
import json
import os
import re
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
from app.verification import fuzzy_match


# ─── Test Calls ─────────────────────────────────────────────────────────────

TEST_CALLS = ["e9a28d20", "42111d0c", "9adbb141"]


# ─── Prompts ────────────────────────────────────────────────────────────────

GENERIC_PROMPT = """You are a compliance auditor. Check the following checkpoints against the transcript.

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

VERBATIM_PROMPT = """You are a compliance auditor checking for EXACT WORDING.

CHECKPOINTS (must match near-exact script wording):
{checkpoints_text}

TRANSCRIPT:
{transcript}

For each checkpoint, the agent must have used words very close to the script.
Minor variations allowed: singular/plural, tense, filler words (um, uh).
Paraphrasing is NOT acceptable for verbatim checkpoints.

- "pass": agent used near-exact wording
- "partial": agent said something related but not close enough to the script
- "fail": agent did not say anything matching this checkpoint

Return ONLY valid JSON array:
[
  {{"name": "checkpoint name", "status": "pass/partial/fail", "evidence": "exact quote", "notes": "null for pass"}}
]"""

MANDATORY_PROMPT = """You are a compliance auditor checking if INFORMATION WAS CONVEYED.

CHECKPOINTS (meaning must be conveyed, exact wording not required):
{checkpoints_text}

TRANSCRIPT:
{transcript}

For each checkpoint, the agent must have conveyed the required information in ANY words.
Paraphrasing, rewording, and natural speech variations are ALL acceptable.
Focus on: did the customer RECEIVE the required information?

- "pass": the information was clearly conveyed
- "partial": some information conveyed but key parts missing
- "fail": the information was not conveyed at all

Return ONLY valid JSON array:
[
  {{"name": "checkpoint name", "status": "pass/partial/fail", "evidence": "exact quote", "notes": "null for pass"}}
]"""

CUSTOMER_YES_PROMPT = """You are a compliance auditor checking for CUSTOMER CONFIRMATION.

CHECKPOINTS (agent must state something AND customer must confirm):
{checkpoints_text}

TRANSCRIPT:
{transcript}

For each checkpoint, TWO things are required:
1. The agent stated the required information
2. The customer gave an affirmative response: "yes", "yeah", "mmhmm", "okay", "right", "sure", "that's fine", "yep", or any clear agreement

- "pass": agent said it AND customer confirmed
- "partial": agent said it but customer didn't clearly confirm (silence, unclear response)
- "fail": agent didn't say it, OR customer explicitly disagreed

Return ONLY valid JSON array:
[
  {{"name": "checkpoint name", "status": "pass/partial/fail", "evidence": "exact quote including customer response", "notes": "null for pass"}}
]"""

SELF_CORRECTION_PROMPT = """You previously judged this checkpoint as "{status}".

CHECKPOINT: {name}
Required: {required}
Strictness: {strictness}

YOUR PREVIOUS EVIDENCE: {evidence}
YOUR PREVIOUS NOTES: {notes}

FULL TRANSCRIPT (re-read carefully):
{transcript}

Re-evaluate this checkpoint. Look for:
- Paraphrased language you may have missed
- Implied meaning or indirect coverage
- Partial coverage that deserves "partial" instead of "fail"
- Exact quotes you overlooked

Are you sure about your original verdict? Return ONLY valid JSON:
{{"name": "{name}", "status": "pass" or "partial" or "fail", "evidence": "quote", "notes": "explanation if changed, null if same"}}"""


# ─── Helpers ────────────────────────────────────────────────────────────────

def fmt_checkpoints(cps):
    text = ""
    for cp in cps:
        text += f"\nCHECKPOINT: {cp['name']}\n"
        text += f"  Required: {cp.get('required', '')}\n"
        text += f"  Strictness: {cp.get('strictness', 'mandatory')}\n"
        text += f"  Key phrases: {', '.join(cp.get('key_phrases', []))}\n"
    return text


def filter_agent_only(transcript: str) -> str:
    """Keep only agent lines from transcript."""
    lines = []
    for line in transcript.split("\n"):
        line_lower = line.lower()
        if "agent" in line_lower or "speaker a" in line_lower or "speaker 0" in line_lower:
            lines.append(line)
        elif not any(kw in line_lower for kw in ["customer", "speaker b", "speaker 1"]):
            lines.append(line)  # keep unlabeled lines
    return "\n".join(lines)


def score_against_gt(results, gt_consensus):
    gt_map = {}
    for c in gt_consensus:
        gt_map[c["name"].lower().strip()] = c["status"]

    matches = mismatches = false_fails = false_passes = partial_disag = 0
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
            details.append(f"  {r.get('name','')[:40]:40s} GT={gs:8s} GOT={rs}")

    total = matches + mismatches
    acc = matches / total * 100 if total else 0
    return {
        "accuracy": round(acc, 1),
        "matches": matches,
        "mismatches": mismatches,
        "total": total,
        "false_fails": false_fails,
        "false_passes": false_passes,
        "partial_disag": partial_disag,
        "details": details,
    }


# ─── Test Runners ───────────────────────────────────────────────────────────

async def run_batched(transcript, checkpoints, prompt_template=GENERIC_PROMPT, batch_size=6, model="anthropic/claude-sonnet-4-6"):
    settings.openrouter_model = model
    batches = [checkpoints[i:i+batch_size] for i in range(0, len(checkpoints), batch_size)]
    all_results = []
    tokens = 0
    t0 = time.time()

    for batch in batches:
        cp_text = fmt_checkpoints(batch)
        prompt = prompt_template.format(checkpoints_text=cp_text, transcript=transcript)
        tokens += len(prompt.split())
        try:
            raw = await _call_llm(prompt, timeout=90.0)
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            results = json.loads(raw)
            tokens += len(raw.split())
            all_results.extend(results)
        except Exception as e:
            for cp in batch:
                all_results.append({"name": cp["name"], "status": "error", "evidence": str(e)[:100]})

    return all_results, time.time() - t0, tokens, len(batches)


async def run_calibrated(transcript, checkpoints, batch_size=6, model="anthropic/claude-sonnet-4-6"):
    """Route each checkpoint to the appropriate prompt by strictness."""
    verbatim = [cp for cp in checkpoints if cp.get("strictness") == "verbatim"]
    mandatory = [cp for cp in checkpoints if cp.get("strictness", "mandatory") == "mandatory"]
    customer_yes = [cp for cp in checkpoints if cp.get("strictness") == "customer_yes"]

    all_results = []
    total_time = 0
    total_tokens = 0
    total_calls = 0

    for cps, prompt in [(verbatim, VERBATIM_PROMPT), (mandatory, MANDATORY_PROMPT), (customer_yes, CUSTOMER_YES_PROMPT)]:
        if not cps:
            continue
        results, t, tok, calls = await run_batched(transcript, cps, prompt, batch_size, model)
        all_results.extend(results)
        total_time += t
        total_tokens += tok
        total_calls += calls

    # Handle any checkpoints without strictness label using generic
    labeled = set(cp["name"] for cp in verbatim + mandatory + customer_yes)
    unlabeled = [cp for cp in checkpoints if cp["name"] not in labeled]
    if unlabeled:
        results, t, tok, calls = await run_batched(transcript, unlabeled, GENERIC_PROMPT, batch_size, model)
        all_results.extend(results)
        total_time += t
        total_tokens += tok
        total_calls += calls

    return all_results, total_time, total_tokens, total_calls


async def run_self_correction(transcript, initial_results, checkpoints, model="anthropic/claude-sonnet-4-6"):
    """Re-check fail/partial results with a review prompt."""
    settings.openrouter_model = model
    cp_map = {cp["name"].lower().strip(): cp for cp in checkpoints}
    corrected = []
    review_count = 0
    tokens = 0
    t0 = time.time()

    for r in initial_results:
        if r.get("status") in ("fail", "partial", "unverified"):
            cp = cp_map.get(r["name"].lower().strip(), {})
            prompt = SELF_CORRECTION_PROMPT.format(
                status=r["status"],
                name=r.get("name", ""),
                required=cp.get("required", ""),
                strictness=cp.get("strictness", "mandatory"),
                evidence=r.get("evidence", ""),
                notes=r.get("notes", ""),
                transcript=transcript,
            )
            tokens += len(prompt.split())
            try:
                raw = await _call_llm(prompt, timeout=60.0)
                if raw.startswith("```"):
                    raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                reviewed = json.loads(raw)
                tokens += len(raw.split())
                if reviewed.get("status") != r["status"]:
                    reviewed["self_corrected"] = True
                corrected.append(reviewed)
                review_count += 1
            except:
                corrected.append(r)
        else:
            corrected.append(r)

    return corrected, time.time() - t0, tokens, review_count


def keyword_prescreen(checkpoints, transcript, entities=None, topics=None):
    """Auto-resolve obvious checkpoints without LLM."""
    transcript_lower = transcript.lower()

    # Build entity lookup
    entity_names = set()
    entity_money = set()
    entity_orgs = set()
    if entities:
        for e in entities:
            et = e.get("entity_type", "")
            text = e.get("text", "").lower()
            entity_names.add(text) if "name" in et else None
            entity_money.add(text) if "money" in et else None
            entity_orgs.add(text) if "org" in et else None

    # Build topic lookup
    topic_words = set()
    if topics:
        for seg in topics:
            for t in seg.get("topics", []):
                for word in t.get("topic", "").lower().split():
                    if len(word) > 3:
                        topic_words.add(word)

    auto_pass = []
    auto_fail = []
    needs_llm = []

    for cp in checkpoints:
        name_lower = cp["name"].lower()
        key_phrases = [kp.lower() for kp in cp.get("key_phrases", [])]

        # Count key phrases found
        found = sum(1 for kp in key_phrases if kp in transcript_lower)
        total = len(key_phrases) if key_phrases else 1

        # Check entity match
        entity_match = False
        if "name" in name_lower and entity_names:
            entity_match = True
        if "supplier" in name_lower and entity_orgs:
            entity_match = True
        if any(word in name_lower for word in ["price", "cost", "charge", "rate"]) and entity_money:
            entity_match = True

        # Check topic match
        topic_match = any(
            tw in name_lower or any(tw in nw for nw in name_lower.split())
            for tw in topic_words
        )

        if found == total and total > 0:
            auto_pass.append({**cp, "prefilter": "auto_pass", "reason": f"All {total} key phrases found"})
        elif found == 0 and not topic_match and not entity_match:
            auto_fail.append({**cp, "prefilter": "auto_fail", "reason": "No phrases, no topic, no entity"})
        else:
            needs_llm.append(cp)

    return auto_pass, auto_fail, needs_llm


# ─── Load Data ──────────────────────────────────────────────────────────────

def load_call_data(short_id):
    db = sqlite3.connect("compliance.db")
    db.row_factory = sqlite3.Row

    call = db.execute(f"SELECT * FROM calls WHERE id LIKE '{short_id}%'").fetchone()
    if not call or not call["script_id"]:
        return None

    script = db.execute("SELECT * FROM scripts WHERE id=?", (call["script_id"],)).fetchone()
    if not script:
        return None

    checkpoints = json.loads(script["checkpoints"])

    # Load GT
    gt_path = f"benchmark/ground_truth/{short_id}_consensus.json"
    if not os.path.exists(gt_path):
        return None
    gt = json.load(open(gt_path))

    # Load transcripts
    transcripts = {
        "deepgram": call["transcript"],
    }

    aai_path = f"transcripts/assemblyai/{short_id}.txt"
    if os.path.exists(aai_path):
        transcripts["assemblyai"] = open(aai_path).read()

    gemini_path = f"transcripts/gemini/{short_id}.txt"
    if os.path.exists(gemini_path):
        transcripts["gemini"] = open(gemini_path).read()

    # Load AAI entities
    aai_full_path = f"transcripts/assemblyai/{short_id}_full.json"
    aai_entities = []
    if os.path.exists(aai_full_path):
        aai_data = json.load(open(aai_full_path))
        aai_entities = aai_data.get("entities", [])

    # Load DG topics
    dg_full_path = f"transcripts/deepgram_full/{short_id}.json"
    dg_topics = []
    if os.path.exists(dg_full_path):
        dg_data = json.load(open(dg_full_path))
        dg_topics = dg_data.get("results", {}).get("topics", {}).get("segments", [])

    db.close()

    return {
        "call": call,
        "script": script,
        "checkpoints": checkpoints,
        "gt": gt,
        "transcripts": transcripts,
        "aai_entities": aai_entities,
        "dg_topics": dg_topics,
        "supplier": call["detected_supplier"] or "Unknown",
    }


# ─── Main ───────────────────────────────────────────────────────────────────

async def run_test(test_num, data, transcript_key="deepgram"):
    """Run a specific test and return results."""
    transcript = data["transcripts"].get(transcript_key, data["transcripts"]["deepgram"])
    cps = data["checkpoints"]
    gt = data["gt"]["consensus"]

    if test_num == 1:  # Batch size 4
        results, t, tok, calls = await run_batched(transcript, cps, batch_size=4)
        return results, t, tok, calls, "batch-4"

    elif test_num == 2:  # Batch size 8
        results, t, tok, calls = await run_batched(transcript, cps, batch_size=8)
        return results, t, tok, calls, "batch-8"

    elif test_num == 3:  # Batch size 12
        results, t, tok, calls = await run_batched(transcript, cps, batch_size=12)
        return results, t, tok, calls, "batch-12"

    elif test_num == 4:  # Strictness-calibrated prompts
        results, t, tok, calls = await run_calibrated(transcript, cps)
        return results, t, tok, calls, "calibrated"

    elif test_num == 5:  # Agent-only transcript
        agent_transcript = filter_agent_only(transcript)
        results, t, tok, calls = await run_batched(agent_transcript, cps)
        return results, t, tok, calls, "agent-only"

    elif test_num == 6:  # Agent-only + calibrated
        agent_transcript = filter_agent_only(transcript)
        results, t, tok, calls = await run_calibrated(agent_transcript, cps)
        return results, t, tok, calls, "agent-only+calibrated"

    elif test_num == 7:  # Self-correction
        initial, t1, tok1, calls1 = await run_batched(transcript, cps)
        corrected, t2, tok2, reviews = await run_self_correction(transcript, initial, cps)
        return corrected, t1 + t2, tok1 + tok2, calls1 + reviews, "self-correction"

    elif test_num == 8:  # Agent-only + calibrated + self-correction
        agent_transcript = filter_agent_only(transcript)
        initial, t1, tok1, calls1 = await run_calibrated(agent_transcript, cps)
        corrected, t2, tok2, reviews = await run_self_correction(transcript, initial, cps)
        return corrected, t1 + t2, tok1 + tok2, calls1 + reviews, "agent+calib+selfcorr"

    elif test_num == 9:  # Keyword pre-screen + #8
        auto_pass, auto_fail, needs_llm = keyword_prescreen(cps, transcript, data["aai_entities"], data["dg_topics"])
        agent_transcript = filter_agent_only(transcript)
        llm_results, t1, tok1, calls1 = await run_calibrated(agent_transcript, needs_llm) if needs_llm else ([], 0, 0, 0)
        corrected, t2, tok2, reviews = await run_self_correction(transcript, llm_results, needs_llm) if llm_results else ([], 0, 0, 0)

        all_results = []
        for cp in auto_pass:
            all_results.append({"name": cp["name"], "status": "pass", "evidence": f"AUTO: {cp['reason']}"})
        for cp in auto_fail:
            all_results.append({"name": cp["name"], "status": "fail", "evidence": f"AUTO: {cp['reason']}"})
        all_results.extend(corrected)

        return all_results, t1 + t2, tok1 + tok2, calls1 + reviews, f"prescreen+full ({len(auto_pass)}P {len(auto_fail)}F {len(needs_llm)}LLM)"

    elif test_num == 10:  # Gemini 2.5 Pro
        results, t, tok, calls = await run_batched(transcript, cps, model="google/gemini-2.5-pro")
        return results, t, tok, calls, "gemini-2.5-pro"

    elif test_num == 11:  # GPT-4.1
        results, t, tok, calls = await run_batched(transcript, cps, model="openai/gpt-4.1-mini")
        return results, t, tok, calls, "gpt-4.1-mini"

    elif test_num == 12:  # AssemblyAI transcript + Sonnet
        aai_transcript = data["transcripts"].get("assemblyai", transcript)
        results, t, tok, calls = await run_batched(aai_transcript, cps)
        return results, t, tok, calls, "aai-transcript+sonnet"

    elif test_num == 13:  # AssemblyAI transcript + full pipeline
        aai_transcript = data["transcripts"].get("assemblyai", transcript)
        agent_transcript = filter_agent_only(aai_transcript)
        initial, t1, tok1, calls1 = await run_calibrated(agent_transcript, cps)
        corrected, t2, tok2, reviews = await run_self_correction(aai_transcript, initial, cps)
        return corrected, t1 + t2, tok1 + tok2, calls1 + reviews, "aai+agent+calib+selfcorr"

    elif test_num == 14:  # MEGA: prescreen + AAI transcript + calibrated + self-correction
        aai_transcript = data["transcripts"].get("assemblyai", transcript)
        auto_pass, auto_fail, needs_llm = keyword_prescreen(cps, aai_transcript, data["aai_entities"], data["dg_topics"])
        agent_transcript = filter_agent_only(aai_transcript)
        llm_results, t1, tok1, calls1 = await run_calibrated(agent_transcript, needs_llm) if needs_llm else ([], 0, 0, 0)
        corrected, t2, tok2, reviews = await run_self_correction(aai_transcript, llm_results, needs_llm) if llm_results else ([], 0, 0, 0)

        all_results = []
        for cp in auto_pass:
            all_results.append({"name": cp["name"], "status": "pass", "evidence": f"AUTO: {cp['reason']}"})
        for cp in auto_fail:
            all_results.append({"name": cp["name"], "status": "fail", "evidence": f"AUTO: {cp['reason']}"})
        all_results.extend(corrected)

        return all_results, t1 + t2, tok1 + tok2, calls1 + reviews, f"MEGA ({len(auto_pass)}P {len(auto_fail)}F {len(needs_llm)}LLM)"


async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", type=int, nargs="+", default=list(range(1, 15)))
    args = parser.parse_args()

    os.makedirs("benchmark/layer2", exist_ok=True)

    print(f"\n{'='*80}")
    print(f"LAYER 2 BENCHMARK — {len(args.test)} tests × {len(TEST_CALLS)} calls")
    print(f"{'='*80}\n")

    csv_rows = []
    test_names = {
        1: "Batch-4", 2: "Batch-8", 3: "Batch-12",
        4: "Calibrated prompts", 5: "Agent-only", 6: "Agent-only + calibrated",
        7: "Self-correction", 8: "Agent+calib+selfcorr", 9: "Prescreen + #8",
        10: "Gemini 2.5 Pro", 11: "GPT-4.1 Mini",
        12: "AAI transcript + Sonnet", 13: "AAI + full pipeline",
        14: "MEGA (AAI+prescreen+calib+selfcorr)",
    }

    # Baseline first
    print(f"--- BASELINE: Deepgram + Sonnet batch-6 ---")
    for short_id in TEST_CALLS:
        data = load_call_data(short_id)
        if not data:
            continue
        results, t, tok, calls = await run_batched(data["transcripts"]["deepgram"], data["checkpoints"])
        score = score_against_gt(results, data["gt"]["consensus"])
        print(f"  {short_id} | {data['supplier']:15s} | {score['accuracy']:>5.1f}% | {t:.1f}s")
        csv_rows.append({
            "test": 0, "test_name": "BASELINE (DG+Sonnet batch-6)",
            "call_id": short_id, "supplier": data["supplier"],
            "accuracy": score["accuracy"], "false_fails": score["false_fails"],
            "false_passes": score["false_passes"], "partial_disag": score["partial_disag"],
            "time_s": round(t, 1), "tokens": tok, "llm_calls": calls,
        })

    # Run each test
    for test_num in args.test:
        name = test_names.get(test_num, f"Test {test_num}")
        print(f"\n--- TEST {test_num}: {name} ---")

        for short_id in TEST_CALLS:
            data = load_call_data(short_id)
            if not data:
                print(f"  {short_id} | SKIP — no data")
                continue

            # Use assemblyai transcript for tests 12-14, deepgram for others
            transcript_key = "assemblyai" if test_num >= 12 else "deepgram"

            try:
                results, t, tok, calls, label = await run_test(test_num, data, transcript_key)
                score = score_against_gt(results, data["gt"]["consensus"])
                print(f"  {short_id} | {data['supplier']:15s} | {score['accuracy']:>5.1f}% | {t:.1f}s | {label}")
                for d in score["details"][:3]:
                    print(f"    {d}")

                csv_rows.append({
                    "test": test_num, "test_name": name,
                    "call_id": short_id, "supplier": data["supplier"],
                    "accuracy": score["accuracy"], "false_fails": score["false_fails"],
                    "false_passes": score["false_passes"], "partial_disag": score["partial_disag"],
                    "time_s": round(t, 1), "tokens": tok, "llm_calls": calls,
                })

                # Save raw results
                with open(f"benchmark/layer2/test{test_num}_{short_id}.json", "w") as f:
                    json.dump(results, f, indent=2)

            except Exception as e:
                print(f"  {short_id} | ERROR: {str(e)[:100]}")

        await asyncio.sleep(1)

    # Save CSV
    csv_path = "benchmark/layer2_results.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "test", "test_name", "call_id", "supplier",
            "accuracy", "false_fails", "false_passes", "partial_disag",
            "time_s", "tokens", "llm_calls",
        ])
        writer.writeheader()
        writer.writerows(csv_rows)

    # Summary
    print(f"\n\n{'='*80}")
    print(f"SUMMARY — Average accuracy per test")
    print(f"{'='*80}")
    print(f"\n{'Test':45s} {'Avg Acc':>8s} {'Avg Time':>9s} {'Avg Calls':>10s}")
    print("-" * 75)

    test_stats = {}
    for row in csv_rows:
        tn = row["test"]
        if tn not in test_stats:
            test_stats[tn] = {"name": row["test_name"], "accs": [], "times": [], "calls": []}
        test_stats[tn]["accs"].append(row["accuracy"])
        test_stats[tn]["times"].append(row["time_s"])
        test_stats[tn]["calls"].append(row["llm_calls"])

    for tn in sorted(test_stats.keys()):
        s = test_stats[tn]
        avg_acc = sum(s["accs"]) / len(s["accs"])
        avg_time = sum(s["times"]) / len(s["times"])
        avg_calls = sum(s["calls"]) / len(s["calls"])
        marker = " <<<" if avg_acc == max(sum(s2["accs"])/len(s2["accs"]) for s2 in test_stats.values()) else ""
        print(f"  {s['name']:43s} {avg_acc:>7.1f}% {avg_time:>8.1f}s {avg_calls:>9.1f}{marker}")

    print(f"\nResults: {csv_path}")
    print(f"Raw: benchmark/layer2/")


if __name__ == "__main__":
    asyncio.run(main())
