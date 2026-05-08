"""
Enriched Transcript: Merge Gemini accuracy + Deepgram intelligence.

Takes:
  - Gemini 2.5 Flash transcript (accurate words)
  - Deepgram Nova-3 full response (speaker IDs, confidence, sentiment, topics, intents, summary)

Produces:
  - Enriched transcript with correct speaker labels, confidence flags, sentiment annotations
  - Pre-filtered checkpoint list (auto-pass/fail obvious ones)
  - Call metadata (summary, topics, intents, sentiment score)

Usage:
    python3 enriched_transcript.py --call-id e9a28d20
    python3 enriched_transcript.py --call-id e9a28d20 --show-flags
"""

import argparse
import json
import os
import re
import sqlite3
from difflib import SequenceMatcher


# ─── Step 1: Parse Gemini Transcript into Lines ────────────────────────────

def parse_gemini_lines(text: str) -> list[dict]:
    """Parse Gemini transcript into structured lines with speaker and timestamp."""
    lines = []
    for raw_line in text.strip().split("\n"):
        raw_line = raw_line.strip()
        if not raw_line:
            continue

        # Extract timestamp [MM:SS] or [HH:MM:SS]
        ts_match = re.match(r'\[?\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*\]?\s*', raw_line)
        timestamp = ts_match.group(1) if ts_match else None
        rest = raw_line[ts_match.end():] if ts_match else raw_line

        # Extract speaker label
        speaker = None
        speaker_match = re.match(r'\*?\*?\s*(Agent|Customer|Speaker\s*\d+)\s*\*?\*?\s*:?\s*', rest, re.IGNORECASE)
        if speaker_match:
            speaker = speaker_match.group(1).strip()
            rest = rest[speaker_match.end():]

        words = rest.split()
        if not words:
            continue

        lines.append({
            "timestamp": timestamp,
            "speaker_gemini": speaker,
            "text": rest,
            "words": words,
        })

    return lines


# ─── Step 2: Build Deepgram Maps ──────────────────────────────────────────

def build_deepgram_maps(raw_response: dict) -> dict:
    """Extract all useful data from Deepgram full response."""
    results = raw_response.get("results", {})
    channels = results.get("channels", [{}])
    alt = channels[0].get("alternatives", [{}])[0] if channels else {}

    # Word-level data
    words = alt.get("words", [])
    word_data = []
    for w in words:
        word_data.append({
            "word": w.get("punctuated_word", w.get("word", "")),
            "start": w.get("start", 0),
            "end": w.get("end", 0),
            "confidence": w.get("confidence", 0),
            "speaker": w.get("speaker", 0),
        })

    # Speaker segments (who speaks when)
    speaker_segments = []
    if word_data:
        current_speaker = word_data[0]["speaker"]
        seg_start = word_data[0]["start"]
        for wd in word_data[1:]:
            if wd["speaker"] != current_speaker:
                speaker_segments.append({
                    "speaker": current_speaker,
                    "start": seg_start,
                    "end": wd["start"],
                })
                current_speaker = wd["speaker"]
                seg_start = wd["start"]
        speaker_segments.append({
            "speaker": current_speaker,
            "start": seg_start,
            "end": word_data[-1]["end"],
        })

    # Confidence map: time → confidence
    confidence_map = [(w["start"], w["end"], w["confidence"], w["word"]) for w in word_data]

    # Sentiment
    sentiments = results.get("sentiments", {})
    sentiment_segments = sentiments.get("segments", [])

    # Topics
    topics = results.get("topics", {})
    topic_segments = topics.get("segments", [])
    all_topics = set()
    for seg in topic_segments:
        for t in seg.get("topics", []):
            all_topics.add(t.get("topic", ""))

    # Intents
    intents = results.get("intents", {})
    intent_segments = intents.get("segments", [])

    # Summary
    summary = results.get("summary", {})

    return {
        "words": word_data,
        "speaker_segments": speaker_segments,
        "confidence_map": confidence_map,
        "sentiment_segments": sentiment_segments,
        "topic_segments": topic_segments,
        "all_topics": all_topics,
        "intent_segments": intent_segments,
        "summary": summary.get("short", summary.get("text", "")),
    }


# ─── Step 3: Map Deepgram Speakers to Agent/Customer ─────────────────────

def identify_speakers(dg_maps: dict) -> dict:
    """Figure out which Deepgram speaker ID is the agent vs customer.

    Heuristic: the speaker who talks more in the first 60 seconds is usually the agent
    (they're doing the intro/script). The other is the customer.
    """
    speaker_words = {}
    for w in dg_maps["words"]:
        if w["start"] < 60:  # first 60 seconds
            sid = w["speaker"]
            speaker_words[sid] = speaker_words.get(sid, 0) + 1

    if not speaker_words:
        return {0: "Agent", 1: "Customer"}

    # Speaker with more words in first 60s is likely the agent
    sorted_speakers = sorted(speaker_words.items(), key=lambda x: -x[1])
    agent_id = sorted_speakers[0][0]

    mapping = {}
    for sid in set(w["speaker"] for w in dg_maps["words"]):
        mapping[sid] = "Agent" if sid == agent_id else "Customer"

    return mapping


# ─── Step 4: Align Gemini Lines with Deepgram Timestamps ─────────────────

def timestamp_to_seconds(ts: str) -> float:
    """Convert MM:SS or HH:MM:SS to seconds."""
    if not ts:
        return 0
    parts = ts.split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    elif len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return 0


def get_speaker_at_time(t: float, speaker_segments: list, speaker_map: dict) -> str:
    """Get the speaker label at a given timestamp."""
    for seg in speaker_segments:
        if seg["start"] <= t <= seg["end"]:
            return speaker_map.get(seg["speaker"], f"Speaker {seg['speaker']}")
    return "Unknown"


def get_confidence_for_text(text: str, confidence_map: list, start_time: float) -> dict:
    """Find confidence scores for words near a timestamp."""
    text_words = text.lower().split()
    if not text_words:
        return {"avg": 1.0, "min": 1.0, "low_words": []}

    # Find Deepgram words near this timestamp
    nearby = [c for c in confidence_map if abs(c[0] - start_time) < 30]
    if not nearby:
        return {"avg": 1.0, "min": 1.0, "low_words": []}

    # Match words by fuzzy text comparison
    matched_confidences = []
    low_words = []
    for tw in text_words:
        best_conf = 1.0
        best_word = tw
        for start, end, conf, dg_word in nearby:
            if tw in dg_word.lower() or dg_word.lower() in tw:
                if conf < best_conf:
                    best_conf = conf
                    best_word = dg_word
        matched_confidences.append(best_conf)
        if best_conf < 0.7:
            low_words.append({"word": best_word, "confidence": round(best_conf, 3)})

    avg_conf = sum(matched_confidences) / len(matched_confidences) if matched_confidences else 1.0
    min_conf = min(matched_confidences) if matched_confidences else 1.0

    return {
        "avg": round(avg_conf, 3),
        "min": round(min_conf, 3),
        "low_words": low_words,
    }


def get_sentiment_at_time(t: float, sentiment_segments: list) -> str:
    """Get sentiment at a given timestamp."""
    for seg in sentiment_segments:
        seg_start = seg.get("start", 0)
        seg_end = seg.get("end", 0)
        if seg_start <= t <= seg_end:
            return seg.get("sentiment", "neutral")
    return "neutral"


# ─── Step 5: Build Enriched Transcript ────────────────────────────────────

def enrich_transcript(gemini_lines: list, dg_maps: dict) -> list[dict]:
    """Merge Gemini text with Deepgram intelligence."""
    speaker_map = identify_speakers(dg_maps)

    enriched = []
    for line in gemini_lines:
        t = timestamp_to_seconds(line.get("timestamp", "0:00"))

        # Get Deepgram speaker for this timestamp
        dg_speaker = get_speaker_at_time(t, dg_maps["speaker_segments"], speaker_map)

        # Get confidence for this line's text
        conf = get_confidence_for_text(line["text"], dg_maps["confidence_map"], t)

        # Get sentiment
        sentiment = get_sentiment_at_time(t, dg_maps["sentiment_segments"])

        enriched.append({
            "timestamp": line.get("timestamp", ""),
            "seconds": t,
            "speaker_gemini": line.get("speaker_gemini", "Unknown"),
            "speaker_deepgram": dg_speaker,
            "speaker": dg_speaker,  # trust Deepgram for speaker ID
            "text": line["text"],
            "confidence_avg": conf["avg"],
            "confidence_min": conf["min"],
            "low_confidence_words": conf["low_words"],
            "sentiment": sentiment,
            "has_flag": conf["min"] < 0.5 or len(conf["low_words"]) > 0,
        })

    return enriched


# ─── Step 6: Pre-Filter Checkpoints ──────────────────────────────────────

def prefilter_checkpoints(checkpoints: list[dict], dg_maps: dict, transcript_text: str) -> list[dict]:
    """Auto-resolve obvious checkpoints without LLM.

    Returns checkpoints with added 'prefilter' field:
    - 'auto_pass': keyword match found all key phrases
    - 'auto_fail': topic not detected AND no keywords found
    - 'needs_llm': ambiguous, send to LLM
    """
    detected_topics = {t.lower() for t in dg_maps["all_topics"]}
    transcript_lower = transcript_text.lower()

    results = []
    for cp in checkpoints:
        name = cp.get("name", "").lower()
        key_phrases = [kp.lower() for kp in cp.get("key_phrases", [])]

        # Count how many key phrases found in transcript
        found = sum(1 for kp in key_phrases if kp in transcript_lower)
        total = len(key_phrases) if key_phrases else 1

        # Check if topic was detected by Deepgram
        topic_match = any(
            topic_word in name or name_word in topic
            for topic in detected_topics
            for topic_word in topic.split()
            for name_word in name.split()
            if len(topic_word) > 3 and len(name_word) > 3
        )

        if found == total and total > 0:
            prefilter = "auto_pass"
            reason = f"All {total} key phrases found in transcript"
        elif found == 0 and not topic_match:
            prefilter = "auto_fail"
            reason = f"No key phrases found, topic not detected by Deepgram"
        else:
            prefilter = "needs_llm"
            reason = f"{found}/{total} key phrases found, topic {'detected' if topic_match else 'not detected'}"

        results.append({
            **cp,
            "prefilter": prefilter,
            "prefilter_reason": reason,
            "key_phrases_found": found,
            "key_phrases_total": total,
            "topic_detected": topic_match,
        })

    return results


# ─── Step 7: Format Enriched Transcript for LLM ──────────────────────────

def format_for_llm(enriched_lines: list[dict]) -> str:
    """Format enriched transcript for LLM consumption."""
    output = []
    for line in enriched_lines:
        ts = line["timestamp"] or "??"
        speaker = line["speaker"]
        text = line["text"]

        # Add confidence flag inline
        flag = ""
        if line["has_flag"]:
            low = ", ".join(f'"{w["word"]}"({w["confidence"]:.0%})' for w in line["low_confidence_words"][:3])
            flag = f" [LOW CONFIDENCE: {low}]"

        # Add sentiment if notable
        sent = ""
        if line["sentiment"] in ("negative",):
            sent = f" [SENTIMENT: {line['sentiment']}]"

        output.append(f"[{ts}] {speaker}: {text}{flag}{sent}")

    return "\n".join(output)


# ─── Main ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Build enriched transcript from Gemini + Deepgram")
    parser.add_argument("--call-id", required=True, help="Call ID prefix")
    parser.add_argument("--show-flags", action="store_true", help="Show flagged lines")
    parser.add_argument("--show-prefilter", action="store_true", help="Show checkpoint pre-filtering")
    args = parser.parse_args()

    short_id = args.call_id[:8]

    # Load Gemini transcript
    gemini_path = f"transcripts/gemini/{short_id}.txt"
    if not os.path.exists(gemini_path):
        print(f"No Gemini transcript found at {gemini_path}")
        return
    gemini_text = open(gemini_path).read()

    # Load Deepgram full response
    dg_path = f"transcripts/deepgram_full/{short_id}.json"
    if not os.path.exists(dg_path):
        print(f"No Deepgram full response found at {dg_path}")
        return
    dg_raw = json.load(open(dg_path))

    # Load script checkpoints
    db = sqlite3.connect("compliance.db")
    db.row_factory = sqlite3.Row
    call = db.execute("SELECT * FROM calls WHERE id LIKE ?", (f"{short_id}%",)).fetchone()
    if not call or not call["script_id"]:
        print(f"No call or script found for {short_id}")
        return
    script = db.execute("SELECT * FROM scripts WHERE id=?", (call["script_id"],)).fetchone()
    checkpoints = json.loads(script["checkpoints"]) if script else []

    print(f"\n{'='*70}")
    print(f"ENRICHED TRANSCRIPT: {short_id}")
    print(f"Supplier: {call['detected_supplier']} | Script: {script['script_name'] if script else 'N/A'}")
    print(f"{'='*70}")

    # Step 1: Parse Gemini
    gemini_lines = parse_gemini_lines(gemini_text)
    print(f"\nGemini lines parsed: {len(gemini_lines)}")

    # Step 2: Build Deepgram maps
    dg_maps = build_deepgram_maps(dg_raw)
    print(f"Deepgram words: {len(dg_maps['words'])}")
    print(f"Deepgram topics: {len(dg_maps['all_topics'])}")
    print(f"Deepgram sentiment segments: {len(dg_maps['sentiment_segments'])}")
    print(f"Summary: {dg_maps['summary'][:100]}...")

    # Step 3: Identify speakers
    speaker_map = identify_speakers(dg_maps)
    print(f"Speaker mapping: {speaker_map}")

    # Step 4: Enrich
    enriched = enrich_transcript(gemini_lines, dg_maps)

    # Stats
    flagged = [l for l in enriched if l["has_flag"]]
    speaker_corrections = sum(1 for l in enriched if l["speaker_gemini"] and l["speaker_deepgram"] and l["speaker_gemini"].lower() != l["speaker_deepgram"].lower())

    print(f"\n--- Enrichment Stats ---")
    print(f"Total lines: {len(enriched)}")
    print(f"Flagged (low confidence): {len(flagged)} ({len(flagged)/len(enriched)*100:.0f}%)")
    print(f"Speaker corrections (Gemini→Deepgram): {speaker_corrections}")

    if args.show_flags and flagged:
        print(f"\n--- Flagged Lines ---")
        for line in flagged:
            low = ", ".join(f'"{w["word"]}"({w["confidence"]:.0%})' for w in line["low_confidence_words"][:3])
            print(f"  [{line['timestamp']}] {line['speaker']}: {line['text'][:60]}...")
            print(f"    Confidence: avg={line['confidence_avg']:.0%} min={line['confidence_min']:.0%} | Low: {low}")

    # Step 5: Pre-filter checkpoints
    if checkpoints:
        filtered = prefilter_checkpoints(checkpoints, dg_maps, gemini_text)
        auto_pass = [c for c in filtered if c["prefilter"] == "auto_pass"]
        auto_fail = [c for c in filtered if c["prefilter"] == "auto_fail"]
        needs_llm = [c for c in filtered if c["prefilter"] == "needs_llm"]

        print(f"\n--- Checkpoint Pre-Filtering ---")
        print(f"Total checkpoints: {len(filtered)}")
        print(f"Auto-pass (keywords found): {len(auto_pass)} ({len(auto_pass)/len(filtered)*100:.0f}%)")
        print(f"Auto-fail (topic not detected): {len(auto_fail)} ({len(auto_fail)/len(filtered)*100:.0f}%)")
        print(f"Needs LLM: {len(needs_llm)} ({len(needs_llm)/len(filtered)*100:.0f}%)")
        print(f"LLM calls saved: {len(auto_pass) + len(auto_fail)}/{len(filtered)}")

        if args.show_prefilter:
            for status, label in [("auto_pass", "AUTO-PASS"), ("auto_fail", "AUTO-FAIL"), ("needs_llm", "NEEDS LLM")]:
                items = [c for c in filtered if c["prefilter"] == status]
                if items:
                    print(f"\n  [{label}]")
                    for c in items:
                        print(f"    {c['name'][:50]:50s} — {c['prefilter_reason']}")

    # Step 6: Format for LLM
    llm_transcript = format_for_llm(enriched)

    # Save outputs
    os.makedirs("transcripts/enriched", exist_ok=True)
    with open(f"transcripts/enriched/{short_id}.txt", "w") as f:
        f.write(llm_transcript)
    with open(f"transcripts/enriched/{short_id}_data.json", "w") as f:
        json.dump({
            "enriched_lines": enriched,
            "prefiltered_checkpoints": filtered if checkpoints else [],
            "speaker_map": speaker_map,
            "topics": list(dg_maps["all_topics"]),
            "summary": dg_maps["summary"],
            "stats": {
                "total_lines": len(enriched),
                "flagged_lines": len(flagged),
                "speaker_corrections": speaker_corrections,
                "auto_pass": len(auto_pass) if checkpoints else 0,
                "auto_fail": len(auto_fail) if checkpoints else 0,
                "needs_llm": len(needs_llm) if checkpoints else 0,
            },
        }, f, indent=2)

    print(f"\n--- Saved ---")
    print(f"Enriched transcript: transcripts/enriched/{short_id}.txt")
    print(f"Full data: transcripts/enriched/{short_id}_data.json")

    # Show sample of enriched transcript
    print(f"\n--- Sample (first 10 lines) ---")
    for line in llm_transcript.split("\n")[:10]:
        print(f"  {line}")

    db.close()


if __name__ == "__main__":
    main()
