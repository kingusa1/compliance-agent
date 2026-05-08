import re
from difflib import SequenceMatcher


def _escape_ilike(value: str) -> str:
    """Escape SQL ILIKE wildcard characters (% and _)."""
    return value.replace("%", r"\%").replace("_", r"\_")


def normalize_text(text: str) -> str:
    """Normalize text for comparison — lowercase, remove extra whitespace and punctuation."""
    text = text.lower()
    text = re.sub(r'[^\w\s]', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def fuzzy_match(transcript: str, claimed_quote: str, threshold: float = 0.75) -> dict:
    """Check if a claimed quote exists in the transcript using fuzzy matching.

    Returns:
        {
            "verified": bool,
            "similarity": float (0-1),
            "best_match": str (the closest matching section of transcript),
        }
    """
    # Explicit "no quote" signal from the analyzer — this is valid for FAIL
    # verdicts where the agent never said the thing. We verify it trivially
    # (there's nothing to match) without claiming a 100% similarity score
    # that the UI would then advertise as "high confidence" next to no quote.
    if claimed_quote == "NOT FOUND IN TRANSCRIPT":
        return {"verified": True, "similarity": 1.0, "best_match": "", "missing_quote": True}

    # Empty evidence on a PASS/PARTIAL is a bug, not a verified quote.
    # Previously we returned {verified: True, similarity: 1.0} which made the
    # UI show "pass · 100% similarity · high confidence" with no text to back
    # it up. Now we flag it as unverified so the caller can downgrade status.
    if not claimed_quote:
        return {"verified": False, "similarity": 0.0, "best_match": "", "missing_quote": True}

    norm_transcript = normalize_text(transcript)
    norm_quote = normalize_text(claimed_quote)

    if not norm_quote:
        return {"verified": False, "similarity": 0.0, "best_match": "", "missing_quote": True}

    # Exact substring match first
    if norm_quote in norm_transcript:
        return {"verified": True, "similarity": 1.0, "best_match": claimed_quote}

    # Sliding window fuzzy match
    quote_words = norm_quote.split()
    transcript_words = norm_transcript.split()
    window_size = len(quote_words)

    if window_size == 0 or len(transcript_words) == 0:
        return {"verified": False, "similarity": 0.0, "best_match": ""}

    best_similarity = 0.0
    best_match = ""

    # Check windows of similar size (±30%)
    for size_offset in range(-max(3, window_size // 3), max(3, window_size // 3) + 1):
        ws = max(1, window_size + size_offset)
        for i in range(len(transcript_words) - ws + 1):
            window = " ".join(transcript_words[i:i + ws])
            similarity = SequenceMatcher(None, norm_quote, window).ratio()
            if similarity > best_similarity:
                best_similarity = similarity
                best_match = window

    return {
        "verified": best_similarity >= threshold,
        "similarity": round(best_similarity, 3),
        "best_match": best_match,
    }


def verify_checkpoint_results(transcript: str, checkpoint_results: list[dict]) -> list[dict]:
    """Verify all checkpoint evidence quotes against the transcript.

    Adds 'verified' and 'similarity' fields to each checkpoint result.
    Changes status to 'unverified' if quote doesn't match transcript.
    """
    verified_results = []

    for cp in checkpoint_results:
        result = dict(cp)

        if cp["status"] == "fail" and cp["evidence"] == "NOT FOUND IN TRANSCRIPT":
            result["verified"] = True
            result["similarity"] = 1.0
        elif cp["status"] in ("pass", "partial"):
            match = fuzzy_match(transcript, cp["evidence"])
            result["verified"] = match["verified"]
            result["similarity"] = match["similarity"]

            # Empty/missing quote on a PASS or PARTIAL verdict is the bug we
            # used to paper over — the AI claimed the checkpoint succeeded
            # but couldn't cite a transcript excerpt to prove it. Downgrade
            # to needs_review and surface that to the reviewer instead of
            # silently displaying "100% similarity, high confidence".
            if match.get("missing_quote"):
                result["needs_review"] = True
                result["confidence"] = "low"
                result["notes"] = (
                    (result.get("notes") or "")
                    + " [AI returned a verdict but could not cite a transcript quote. "
                      "Needs a human to listen to the call and decide.]"
                )
            elif not match["verified"]:
                result["status"] = "unverified"
                result["notes"] = (result.get("notes") or "") + f" [QUOTE NOT VERIFIED - similarity: {match['similarity']}]"
        else:
            result["verified"] = True
            result["similarity"] = 1.0

        verified_results.append(result)

    return verified_results
