"""
Dual transcription: Gemini Flash via OpenRouter for all calls.
Saves Gemini transcripts alongside existing Deepgram transcripts.
Generates comparison CSV with per-call stats.

Usage:
    python3 transcribe_all.py                    # all calls
    python3 transcribe_all.py --call-id e9a28d20 # specific call
    python3 transcribe_all.py --skip-existing    # skip already transcribed
"""

import argparse
import base64
import csv
import json
import os
import sqlite3
import sys
import time
from datetime import datetime
from difflib import SequenceMatcher

import httpx

OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY")
if not OPENROUTER_KEY:
    raise SystemExit("OPENROUTER_API_KEY env var required")
MODEL = "google/gemini-2.5-flash"

TRANSCRIPTION_PROMPT = """Transcribe this audio exactly word for word. This is a compliance call between an energy broker agent and a customer.

Rules:
- Include EVERY word spoken, including filler words (um, uh, yeah, mmhmm)
- Label each line with the speaker: [Agent] or [Customer]
- Include timestamps in [MM:SS] format at the start of each speaker turn
- Do not skip or summarize any section
- Preserve exact wording — do not paraphrase or clean up grammar
- If you can't make out a word, write [inaudible]"""


def transcribe_with_gemini(audio_path: str) -> dict:
    """Transcribe audio file with Gemini Flash via OpenRouter."""
    start = time.time()

    with open(audio_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    ext = os.path.splitext(audio_path)[1].lstrip(".")
    if ext == "mp3":
        fmt = "mp3"
    elif ext == "wav":
        fmt = "wav"
    elif ext == "m4a":
        fmt = "m4a"
    else:
        fmt = ext

    file_size_mb = os.path.getsize(audio_path) / (1024 * 1024)
    print(f"    Sending {file_size_mb:.1f}MB {fmt} to {MODEL}...")

    with httpx.Client(timeout=180.0) as client:
        r = client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": MODEL,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": TRANSCRIPTION_PROMPT},
                            {
                                "type": "input_audio",
                                "input_audio": {"data": b64, "format": fmt},
                            },
                        ],
                    }
                ],
                "temperature": 0,
                "max_tokens": 16384,
            },
        )
        r.raise_for_status()

    elapsed = time.time() - start
    data = r.json()
    transcript = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})

    return {
        "transcript": transcript,
        "time_seconds": round(elapsed, 1),
        "cost": usage.get("cost", 0),
        "total_tokens": usage.get("total_tokens", 0),
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "model": MODEL,
    }


def compare_transcripts(deepgram: str, gemini: str) -> dict:
    """Compare two transcripts and return similarity stats."""
    # Normalize for comparison
    dg_words = deepgram.lower().split()
    gm_words = gemini.lower().split()

    # Overall similarity
    matcher = SequenceMatcher(None, dg_words, gm_words)
    similarity = matcher.ratio()

    # Word counts
    dg_count = len(dg_words)
    gm_count = len(gm_words)

    # Find disagreements (simplified)
    opcodes = matcher.get_opcodes()
    replacements = []
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "replace":
            dg_chunk = " ".join(dg_words[i1:i2])
            gm_chunk = " ".join(gm_words[j1:j2])
            replacements.append({"deepgram": dg_chunk, "gemini": gm_chunk})

    return {
        "similarity": round(similarity, 4),
        "deepgram_words": dg_count,
        "gemini_words": gm_count,
        "word_diff": gm_count - dg_count,
        "disagreements": len(replacements),
        "top_disagreements": replacements[:10],
    }


def main():
    parser = argparse.ArgumentParser(description="Dual transcription with Gemini Flash")
    parser.add_argument("--call-id", default=None, help="Specific call ID prefix")
    parser.add_argument("--skip-existing", action="store_true", help="Skip calls already transcribed with Gemini")
    args = parser.parse_args()

    db = sqlite3.connect("compliance.db")
    db.row_factory = sqlite3.Row

    if args.call_id:
        calls = db.execute(
            "SELECT id, filename, file_path, detected_supplier, transcript FROM calls WHERE id LIKE ? AND transcript IS NOT NULL",
            (f"{args.call_id}%",),
        ).fetchall()
    else:
        calls = db.execute(
            "SELECT id, filename, file_path, detected_supplier, transcript FROM calls WHERE transcript IS NOT NULL"
        ).fetchall()

    os.makedirs("transcripts/gemini", exist_ok=True)
    os.makedirs("transcripts/deepgram", exist_ok=True)
    os.makedirs("transcripts/comparison", exist_ok=True)

    csv_path = "transcripts/transcript_comparison.csv"
    csv_exists = os.path.exists(csv_path)
    csv_fields = [
        "timestamp", "call_id", "filename", "supplier",
        "deepgram_words", "gemini_words", "word_diff",
        "similarity", "disagreements",
        "gemini_time_s", "gemini_cost", "gemini_tokens",
        "model",
    ]
    csv_file = open(csv_path, "a", newline="")
    writer = csv.DictWriter(csv_file, fieldnames=csv_fields)
    if not csv_exists:
        writer.writeheader()

    print(f"\n{'='*70}")
    print(f"DUAL TRANSCRIPTION: {len(calls)} calls")
    print(f"Model: {MODEL}")
    print(f"{'='*70}\n")

    total_cost = 0
    total_time = 0

    for i, call in enumerate(calls):
        call_id = call["id"]
        short_id = call_id[:8]
        supplier = call["detected_supplier"] or "Unknown"
        filename = call["filename"]
        audio_path = call["file_path"]

        gemini_path = f"transcripts/gemini/{short_id}.txt"

        if args.skip_existing and os.path.exists(gemini_path):
            print(f"[{i+1}/{len(calls)}] SKIP {short_id} — already transcribed")
            continue

        if not os.path.exists(audio_path):
            print(f"[{i+1}/{len(calls)}] SKIP {short_id} — audio file missing: {audio_path}")
            continue

        print(f"[{i+1}/{len(calls)}] {short_id} | {supplier} | {filename[:50]}...")

        # Save Deepgram transcript
        dg_path = f"transcripts/deepgram/{short_id}.txt"
        with open(dg_path, "w") as f:
            f.write(call["transcript"])

        # Transcribe with Gemini
        try:
            result = transcribe_with_gemini(audio_path)
        except Exception as e:
            print(f"    FAILED: {e}")
            # Rate limit — wait and retry once
            if "429" in str(e):
                print(f"    Rate limited — waiting 30s and retrying...")
                time.sleep(30)
                try:
                    result = transcribe_with_gemini(audio_path)
                except Exception as e2:
                    print(f"    FAILED again: {e2}")
                    continue
            else:
                continue

        # Save Gemini transcript
        with open(gemini_path, "w") as f:
            f.write(result["transcript"])

        total_cost += result["cost"]
        total_time += result["time_seconds"]

        print(f"    Gemini: {result['time_seconds']}s | ${result['cost']:.4f} | {result['total_tokens']} tokens")

        # Compare
        comparison = compare_transcripts(call["transcript"], result["transcript"])
        print(f"    Similarity: {comparison['similarity']*100:.1f}% | DG: {comparison['deepgram_words']} words | GM: {comparison['gemini_words']} words | {comparison['disagreements']} disagreements")

        # Save comparison details
        comp_path = f"transcripts/comparison/{short_id}.json"
        with open(comp_path, "w") as f:
            json.dump({
                "call_id": call_id,
                "supplier": supplier,
                "filename": filename,
                "similarity": comparison["similarity"],
                "deepgram_words": comparison["deepgram_words"],
                "gemini_words": comparison["gemini_words"],
                "disagreements": comparison["disagreements"],
                "top_disagreements": comparison["top_disagreements"],
                "gemini_time": result["time_seconds"],
                "gemini_cost": result["cost"],
                "gemini_tokens": result["total_tokens"],
            }, f, indent=2)

        # Write CSV row
        writer.writerow({
            "timestamp": datetime.now().isoformat(),
            "call_id": short_id,
            "filename": filename[:60],
            "supplier": supplier,
            "deepgram_words": comparison["deepgram_words"],
            "gemini_words": comparison["gemini_words"],
            "word_diff": comparison["word_diff"],
            "similarity": comparison["similarity"],
            "disagreements": comparison["disagreements"],
            "gemini_time_s": result["time_seconds"],
            "gemini_cost": result["cost"],
            "gemini_tokens": result["total_tokens"],
            "model": MODEL,
        })
        csv_file.flush()

        # Small delay to avoid rate limits
        if i < len(calls) - 1:
            time.sleep(2)

    csv_file.close()

    print(f"\n{'='*70}")
    print(f"DONE")
    print(f"Total time: {total_time:.1f}s")
    print(f"Total cost: ${total_cost:.4f}")
    print(f"Results: {csv_path}")
    print(f"Gemini transcripts: transcripts/gemini/")
    print(f"Comparisons: transcripts/comparison/")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
