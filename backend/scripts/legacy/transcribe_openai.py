"""
Third transcription source: OpenAI GPT Audio Mini via OpenRouter.
Runs all calls and saves to transcripts/openai/ for three-way comparison.

Usage:
    python3 transcribe_openai.py
    python3 transcribe_openai.py --call-id e9a28d20
"""

import argparse
import base64
import csv
import json
import os
import sqlite3
import time
from datetime import datetime
from difflib import SequenceMatcher

import httpx

OPENROUTER_KEY = os.environ.get(
    "OPENROUTER_API_KEY",
    "REDACTED-LEAKED-OPENROUTER-KEY-ROTATED-2026-05-18",
)
MODEL = "openai/gpt-audio-mini"

PROMPT = """Transcribe this audio exactly word for word. This is a compliance call between an energy broker agent and a customer.

Rules:
- Include EVERY word spoken, including filler words (um, uh, yeah, mmhmm)
- Label each line with the speaker: [Agent] or [Customer]
- Include timestamps in [MM:SS] format at the start of each speaker turn
- Do not skip or summarize any section
- Preserve exact wording — do not paraphrase or clean up grammar
- If you can't make out a word, write [inaudible]"""


def transcribe(audio_path: str) -> dict:
    start = time.time()
    with open(audio_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    ext = os.path.splitext(audio_path)[1].lstrip(".")
    size_mb = os.path.getsize(audio_path) / (1024 * 1024)
    print(f"    Sending {size_mb:.1f}MB {ext} to {MODEL}...")

    with httpx.Client(timeout=180.0) as client:
        r = client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": MODEL,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": PROMPT},
                        {"type": "input_audio", "input_audio": {"data": b64, "format": ext}},
                    ],
                }],
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
    }


def normalize(text: str) -> list[str]:
    """Normalize transcript for comparison — strip timestamps, labels, lowercase."""
    import re
    text = re.sub(r'\[?\d{1,2}:\d{2}(:\d{2})?\]?', '', text)
    text = re.sub(r'\[?(Agent|Customer|Speaker\s*\d+)\]?:?', '', text, flags=re.IGNORECASE)
    return text.lower().split()


def similarity(a: str, b: str) -> float:
    wa = normalize(a)
    wb = normalize(b)
    return SequenceMatcher(None, wa, wb).ratio()


def three_way_compare(dg: str, gm: str, oai: str) -> dict:
    """Compare all three transcripts."""
    dg_gm = similarity(dg, gm)
    dg_oai = similarity(dg, oai)
    gm_oai = similarity(gm, oai)

    # The two that agree most are probably right
    pairs = [
        ("deepgram-gemini", dg_gm),
        ("deepgram-openai", dg_oai),
        ("gemini-openai", gm_oai),
    ]
    best_pair = max(pairs, key=lambda x: x[1])

    return {
        "deepgram_vs_gemini": round(dg_gm, 4),
        "deepgram_vs_openai": round(dg_oai, 4),
        "gemini_vs_openai": round(gm_oai, 4),
        "most_agreeing_pair": best_pair[0],
        "best_agreement": round(best_pair[1], 4),
        "deepgram_words": len(normalize(dg)),
        "gemini_words": len(normalize(gm)),
        "openai_words": len(normalize(oai)),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--call-id", default=None)
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

    os.makedirs("transcripts/openai", exist_ok=True)
    os.makedirs("transcripts/three_way", exist_ok=True)

    csv_path = "transcripts/three_way_comparison.csv"
    csv_exists = os.path.exists(csv_path)
    fields = [
        "timestamp", "call_id", "supplier",
        "dg_words", "gm_words", "oai_words",
        "dg_vs_gm", "dg_vs_oai", "gm_vs_oai",
        "most_agreeing", "best_agreement",
        "oai_time_s", "oai_cost", "oai_tokens",
    ]
    csv_file = open(csv_path, "a", newline="")
    writer = csv.DictWriter(csv_file, fieldnames=fields)
    if not csv_exists:
        writer.writeheader()

    print(f"\n{'='*70}")
    print(f"THREE-WAY TRANSCRIPTION: {len(calls)} calls")
    print(f"Third source: {MODEL}")
    print(f"{'='*70}\n")

    total_cost = 0

    for i, call in enumerate(calls):
        short_id = call["id"][:8]
        supplier = call["detected_supplier"] or "Unknown"
        audio_path = call["file_path"]

        if not os.path.exists(audio_path):
            print(f"[{i+1}/{len(calls)}] SKIP {short_id} — no audio")
            continue

        # Check if Gemini transcript exists
        gm_path = f"transcripts/gemini/{short_id}.txt"
        if not os.path.exists(gm_path):
            print(f"[{i+1}/{len(calls)}] SKIP {short_id} — no Gemini transcript")
            continue

        print(f"[{i+1}/{len(calls)}] {short_id} | {supplier}")

        # Transcribe with OpenAI
        try:
            result = transcribe(audio_path)
        except Exception as e:
            if "429" in str(e):
                print(f"    Rate limited — waiting 30s...")
                time.sleep(30)
                try:
                    result = transcribe(audio_path)
                except Exception as e2:
                    print(f"    FAILED: {e2}")
                    continue
            else:
                print(f"    FAILED: {e}")
                continue

        # Save OpenAI transcript
        oai_path = f"transcripts/openai/{short_id}.txt"
        with open(oai_path, "w") as f:
            f.write(result["transcript"])

        total_cost += result["cost"]
        print(f"    OpenAI: {result['time_seconds']}s | ${result['cost']:.4f}")

        # Three-way comparison
        with open(gm_path) as f:
            gm_text = f.read()

        comp = three_way_compare(call["transcript"], gm_text, result["transcript"])

        print(f"    DG↔GM: {comp['deepgram_vs_gemini']*100:.1f}% | DG↔OAI: {comp['deepgram_vs_openai']*100:.1f}% | GM↔OAI: {comp['gemini_vs_openai']*100:.1f}%")
        print(f"    Winner pair: {comp['most_agreeing_pair']} ({comp['best_agreement']*100:.1f}%)")

        # Save comparison
        with open(f"transcripts/three_way/{short_id}.json", "w") as f:
            json.dump(comp, f, indent=2)

        writer.writerow({
            "timestamp": datetime.now().isoformat(),
            "call_id": short_id,
            "supplier": supplier,
            "dg_words": comp["deepgram_words"],
            "gm_words": comp["gemini_words"],
            "oai_words": comp["openai_words"],
            "dg_vs_gm": comp["deepgram_vs_gemini"],
            "dg_vs_oai": comp["deepgram_vs_openai"],
            "gm_vs_oai": comp["gemini_vs_openai"],
            "most_agreeing": comp["most_agreeing_pair"],
            "best_agreement": comp["best_agreement"],
            "oai_time_s": result["time_seconds"],
            "oai_cost": result["cost"],
            "oai_tokens": result["total_tokens"],
        })
        csv_file.flush()

        if i < len(calls) - 1:
            time.sleep(2)

    csv_file.close()
    print(f"\n{'='*70}")
    print(f"DONE | Total cost: ${total_cost:.4f}")
    print(f"Results: {csv_path}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
