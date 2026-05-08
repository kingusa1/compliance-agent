"""
Re-transcribe all calls with Deepgram, saving the FULL response including:
- Per-word confidence scores
- Per-word timestamps
- Speaker diarization
- Punctuated words
- Sentiment analysis
- Topic detection
- Intent recognition
- Summarization

Saves raw JSON responses to transcripts/deepgram_full/

Usage:
    python3 transcribe_deepgram_full.py
    python3 transcribe_deepgram_full.py --call-id e9a28d20
"""

import argparse
import csv
import json
import os
import sqlite3
import time
from datetime import datetime

from deepgram import DeepgramClient, PrerecordedOptions

from app.config import settings
from app.logger import log


def transcribe_full(file_path: str) -> dict:
    """Transcribe with ALL Deepgram features enabled."""
    client = DeepgramClient(settings.deepgram_api_key)

    with open(file_path, "rb") as f:
        source = {"buffer": f.read()}

    options = PrerecordedOptions(
        model="nova-2",
        diarize=True,
        punctuate=True,
        smart_format=True,
        # Intelligence features
        sentiment=True,
        intents=True,
        topics=True,
        summarize="v2",
    )

    start = time.time()
    log.info(f"DEEPGRAM FULL calling Nova-2 with ALL features...")

    import asyncio
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    response = loop.run_until_complete(
        client.listen.asyncrest.v("1").transcribe_file(source, options)
    )

    elapsed = time.time() - start

    # Convert to dict for JSON serialization
    raw = response.to_dict() if hasattr(response, 'to_dict') else json.loads(str(response))

    return {
        "raw_response": raw,
        "time_seconds": round(elapsed, 1),
    }


def extract_word_stats(raw: dict) -> dict:
    """Extract word-level confidence stats from raw Deepgram response."""
    try:
        words = raw["results"]["channels"][0]["alternatives"][0]["words"]
    except (KeyError, IndexError):
        return {"error": "Could not find words in response"}

    confidences = [w.get("confidence", 0) for w in words]
    if not confidences:
        return {"error": "No confidence scores found"}

    avg_conf = sum(confidences) / len(confidences)
    below_85 = sum(1 for c in confidences if c < 0.85)
    below_70 = sum(1 for c in confidences if c < 0.70)
    below_50 = sum(1 for c in confidences if c < 0.50)
    min_conf = min(confidences)
    max_conf = max(confidences)

    # Find the lowest confidence words
    word_confs = [(w.get("punctuated_word", w.get("word", "")), w.get("confidence", 0), w.get("start", 0))
                  for w in words]
    worst_words = sorted(word_confs, key=lambda x: x[1])[:10]

    return {
        "total_words": len(words),
        "avg_confidence": round(avg_conf, 4),
        "min_confidence": round(min_conf, 4),
        "max_confidence": round(max_conf, 4),
        "below_0.85": below_85,
        "below_0.85_pct": round(below_85 / len(words) * 100, 1),
        "below_0.70": below_70,
        "below_0.70_pct": round(below_70 / len(words) * 100, 1),
        "below_0.50": below_50,
        "below_0.50_pct": round(below_50 / len(words) * 100, 1),
        "worst_10_words": [{"word": w, "confidence": c, "at_seconds": round(t, 1)} for w, c, t in worst_words],
    }


def extract_intelligence(raw: dict) -> dict:
    """Extract intelligence features from raw response."""
    results = raw.get("results", {})

    # Sentiments
    sentiments = results.get("sentiments", {})
    sentiment_segments = sentiments.get("segments", [])
    sentiment_avg = sentiments.get("average", {})

    # Topics
    topics = results.get("topics", {})
    topic_segments = topics.get("segments", [])

    # Intents
    intents = results.get("intents", {})
    intent_segments = intents.get("segments", [])

    # Summary
    summary = results.get("summary", {})

    return {
        "sentiment_segments": len(sentiment_segments),
        "sentiment_average": sentiment_avg,
        "sentiment_sample": sentiment_segments[:3] if sentiment_segments else [],
        "topics_found": len(topic_segments),
        "topics_sample": topic_segments[:5] if topic_segments else [],
        "intents_found": len(intent_segments),
        "intents_sample": intent_segments[:5] if intent_segments else [],
        "summary": summary.get("short", summary.get("text", "")),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--call-id", default=None)
    args = parser.parse_args()

    db = sqlite3.connect("compliance.db")
    db.row_factory = sqlite3.Row

    if args.call_id:
        calls = db.execute(
            "SELECT id, filename, file_path, detected_supplier FROM calls WHERE id LIKE ? AND transcript IS NOT NULL",
            (f"{args.call_id}%",),
        ).fetchall()
    else:
        calls = db.execute(
            "SELECT id, filename, file_path, detected_supplier FROM calls WHERE transcript IS NOT NULL"
        ).fetchall()

    os.makedirs("transcripts/deepgram_full", exist_ok=True)

    csv_path = "transcripts/deepgram_confidence.csv"
    csv_exists = os.path.exists(csv_path)
    fields = [
        "timestamp", "call_id", "supplier",
        "total_words", "avg_confidence",
        "min_confidence", "max_confidence",
        "below_85", "below_85_pct",
        "below_70", "below_70_pct",
        "below_50", "below_50_pct",
        "sentiment_segments", "topics_found", "intents_found",
        "has_summary", "time_seconds",
    ]
    csv_file = open(csv_path, "a", newline="")
    writer = csv.DictWriter(csv_file, fieldnames=fields)
    if not csv_exists:
        writer.writeheader()

    print(f"\n{'='*70}")
    print(f"DEEPGRAM FULL INTELLIGENCE: {len(calls)} calls")
    print(f"Features: confidence + sentiment + topics + intents + summary")
    print(f"{'='*70}\n")

    total_cost = 0

    for i, call in enumerate(calls):
        short_id = call["id"][:8]
        supplier = call["detected_supplier"] or "Unknown"
        audio_path = call["file_path"]

        if not os.path.exists(audio_path):
            print(f"[{i+1}/{len(calls)}] SKIP {short_id} — no audio")
            continue

        # Skip duplicates (same file, different DB entry)
        out_path = f"transcripts/deepgram_full/{short_id}.json"
        if os.path.exists(out_path):
            print(f"[{i+1}/{len(calls)}] SKIP {short_id} — already done")
            continue

        print(f"[{i+1}/{len(calls)}] {short_id} | {supplier} | {call['filename'][:50]}...")

        try:
            result = transcribe_full(audio_path)
        except Exception as e:
            print(f"    FAILED: {e}")
            continue

        raw = result["raw_response"]

        # Save full raw response
        with open(out_path, "w") as f:
            json.dump(raw, f)

        # Extract stats
        word_stats = extract_word_stats(raw)
        intel = extract_intelligence(raw)

        print(f"    Time: {result['time_seconds']}s")
        print(f"    Words: {word_stats.get('total_words', '?')} | Avg confidence: {word_stats.get('avg_confidence', '?')}")
        print(f"    Below 0.85: {word_stats.get('below_85', '?')} ({word_stats.get('below_85_pct', '?')}%)")
        print(f"    Below 0.70: {word_stats.get('below_70', '?')} ({word_stats.get('below_70_pct', '?')}%)")
        print(f"    Sentiment segments: {intel['sentiment_segments']} | Topics: {intel['topics_found']} | Intents: {intel['intents_found']}")
        if intel["summary"]:
            print(f"    Summary: {intel['summary'][:100]}...")

        if word_stats.get("worst_10_words"):
            print(f"    Worst confidence words:")
            for w in word_stats["worst_10_words"][:5]:
                print(f"      \"{w['word']}\" → {w['confidence']:.3f} at {w['at_seconds']}s")

        # Save extracted stats
        stats_path = f"transcripts/deepgram_full/{short_id}_stats.json"
        with open(stats_path, "w") as f:
            json.dump({"word_stats": word_stats, "intelligence": intel}, f, indent=2)

        # CSV
        writer.writerow({
            "timestamp": datetime.now().isoformat(),
            "call_id": short_id,
            "supplier": supplier,
            "total_words": word_stats.get("total_words", 0),
            "avg_confidence": word_stats.get("avg_confidence", 0),
            "min_confidence": word_stats.get("min_confidence", 0),
            "max_confidence": word_stats.get("max_confidence", 0),
            "below_85": word_stats.get("below_0.85", 0),
            "below_85_pct": word_stats.get("below_0.85_pct", 0),
            "below_70": word_stats.get("below_0.70", 0),
            "below_70_pct": word_stats.get("below_0.70_pct", 0),
            "below_50": word_stats.get("below_0.50", 0),
            "below_50_pct": word_stats.get("below_0.50_pct", 0),
            "sentiment_segments": intel["sentiment_segments"],
            "topics_found": intel["topics_found"],
            "intents_found": intel["intents_found"],
            "has_summary": bool(intel["summary"]),
            "time_seconds": result["time_seconds"],
        })
        csv_file.flush()

        time.sleep(1)

    csv_file.close()
    print(f"\n{'='*70}")
    print(f"DONE")
    print(f"Full responses: transcripts/deepgram_full/")
    print(f"Confidence CSV: {csv_path}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
