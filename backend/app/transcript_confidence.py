"""
Cross-reference Deepgram word confidence scores with checkpoint evidence.
Flags low-confidence words that fall within evidence quotes.
"""

from app.logger import log


def flag_low_confidence_evidence(
    evidence: str,
    deepgram_words: list[dict],
    threshold: float = 0.70,
) -> dict:
    """Check if evidence quote contains low-confidence words from Deepgram.

    Args:
        evidence: The quote extracted by the LLM as checkpoint evidence.
        deepgram_words: List of Deepgram word dicts with 'word', 'confidence', 'start'.
        threshold: Confidence threshold below which words are flagged.

    Returns:
        dict with 'has_flags', 'avg_confidence', 'low_words', 'flag_message'.
    """
    if not evidence or not deepgram_words:
        return {"has_flags": False, "avg_confidence": 1.0, "low_words": [], "flag_message": None}

    evidence_words = set(evidence.lower().split())

    # Filter common words that are always low confidence (articles, pronouns)
    noise_words = {"i", "a", "the", "at", "to", "of", "in", "is", "it", "and", "or", "for", "on", "you", "we", "he", "she", "my", "your"}
    evidence_words -= noise_words

    if not evidence_words:
        return {"has_flags": False, "avg_confidence": 1.0, "low_words": [], "flag_message": None}

    # Find matching Deepgram words and their confidence
    matched = []
    low_words = []

    for dw in deepgram_words:
        dw_word = dw.get("word", "").lower()
        if dw_word in evidence_words:
            conf = dw.get("confidence", 1.0)
            matched.append(conf)
            if conf < threshold:
                low_words.append({
                    "word": dw.get("punctuated_word", dw.get("word", "")),
                    "confidence": round(conf, 3),
                    "start": round(dw.get("start", 0), 1),
                })

    avg_conf = sum(matched) / len(matched) if matched else 1.0
    has_flags = len(low_words) > 0

    flag_message = None
    if has_flags:
        flagged = ", ".join(f'"{w["word"]}" ({w["confidence"]:.0%})' for w in low_words[:3])
        flag_message = f"Low confidence words in evidence: {flagged}. Transcript may be inaccurate here."

    return {
        "has_flags": has_flags,
        "avg_confidence": round(avg_conf, 3),
        "low_words": low_words,
        "flag_message": flag_message,
    }


def annotate_checkpoint_results(
    results: list[dict],
    deepgram_words: list[dict],
    threshold: float = 0.70,
) -> list[dict]:
    """Add confidence flags to checkpoint results.

    Modifies results in place, adding 'transcript_confidence' field to each.
    """
    for r in results:
        evidence = r.get("evidence", "")
        flags = flag_low_confidence_evidence(evidence, deepgram_words, threshold)
        r["transcript_confidence"] = flags

        if flags["has_flags"] and r["status"] in ("fail", "unverified"):
            log.warning(
                f"\u26a0\ufe0f CONFIDENCE flag on \"{r.get('name', '')}\": "
                f"verdict={r['status']} but evidence has low-confidence words. "
                f"{flags['flag_message']}"
            )

    return results
