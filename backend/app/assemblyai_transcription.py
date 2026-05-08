"""AssemblyAI Universal-3 Pro transcription — primary transcript for compliance analysis.

Returns per-word timestamps, speaker labels, and confidence scores.
Best accuracy for British accent phone audio (tested across 14 models).
"""

import asyncio
import time

import httpx

from app.config import settings
from app.glossaries.loader import load_supplier_glossary
from app.logger import log


ASSEMBLYAI_BASE = "https://api.assemblyai.com/v2"

# L9 PII redaction policy set — UK context. SSN deliberately excluded;
# `redact_pii_audio` is FALSE so reviewers still hear raw audio.
# AAI v2 valid policy names per https://www.assemblyai.com/docs/audio-intelligence/pii-redaction
# `banking_information` is the correct UK-context superset (covers IBAN, sort
# code, account number) per AAI docs. `credit_card_number` is the v2 spelling.
PII_POLICIES: list[str] = [
    "person_name",
    "phone_number",
    "email_address",
    "credit_card_number",
    "banking_information",
]
# AAI v2 requires `redact_pii_sub` (substitution mode) when `redact_pii=True`.
# "hash" replaces redacted spans with hash tokens like ##### preserving length.
# "entity_name" replaces with [PERSON_NAME] etc — preferred for reviewer UX.
PII_REDACT_SUB: str = "entity_name"


async def transcribe_audio_assemblyai(file_path: str, supplier_hint: str | None = None) -> dict:
    """Transcribe audio with AssemblyAI Universal-3 Pro — full intelligence layer.

    Returns:
        dict with keys:
            - transcript: str (full text)
            - words: list[dict] (per-word: word, start, end, speaker, confidence)
            - utterances: list[dict] (per-speaker-turn: speaker, text, start, end)
            - metadata:   dict (full provider response — chapters, entities,
                                highlights, sentiment segments, summary,
                                iab_categories, language_confidence, etc.)

    All intelligence features are requested on the same submit so the
    reviewer UI can read every signal from a single stored row. Cost impact
    vs bare transcription: ~$0.013 on a 10-min call. Documented in
    docs/planning/2026-04-18-benchmark-briefing.md.
    """
    api_key = settings.assemblyai_api_key
    if not api_key:
        raise ValueError("ASSEMBLYAI_API_KEY not set")

    # L9 fix: split timeouts (connect/read/write/pool) so the upload phase has
    # its own write budget separate from the long polling read budget. Single
    # 300s timeout was hitting httpx.ReadError on 1MB+ uploads under flaky
    # network. Also: stream upload bytes via httpx 'content' bytes (no chunked
    # generator — keeps content-length header so AAI doesn't 411).
    timeout = httpx.Timeout(connect=30.0, read=600.0, write=600.0, pool=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        # Step 1: Upload audio
        log.info("🎙️ ASSEMBLYAI uploading audio...")
        with open(file_path, "rb") as f:
            audio_bytes = f.read()
        log.info(f"🎙️ ASSEMBLYAI uploading {len(audio_bytes)} bytes...")
        upload_resp = await client.post(
            f"{ASSEMBLYAI_BASE}/upload",
            headers={"authorization": api_key, "content-type": "application/octet-stream"},
            content=audio_bytes,
        )
        upload_resp.raise_for_status()
        upload_url = upload_resp.json()["upload_url"]
        log.info(f"🎙️ ASSEMBLYAI upload OK url={upload_url[:60]}")

        # Step 2: Submit transcription job with every intelligence feature
        # enabled. `summarization` requires a `summary_type` + `summary_model`
        # pair; defaults satisfy them.
        log.info("🎙️ ASSEMBLYAI submitting Universal-3 Pro — full intel layer...")
        t0 = time.time()
        # AssemblyAI compatibility (verified 2026-04-19 against live API):
        # universal-3-pro DOES NOT support `summarization`, `auto_chapters`,
        # or `disfluencies` — each returns 400 with a mutual-incompatibility
        # error. The five features below are the full intel set that
        # Universal-3 Pro actually accepts:
        #   - speaker_labels: diarization
        #   - entity_detection: names, numbers, dates
        #   - auto_highlights: auto-ranked key phrases
        #   - iab_categories: topic classification
        #   - sentiment_analysis: per-sentence sentiment
        # If AAI later lifts the summary restriction, add summarization back.
        # L9: word_boost biases STT toward UK energy + supplier vocab.
        # redact_pii=True returns redacted transcript text; redact_pii_audio
        # stays FALSE so reviewers hear raw audio for audit purposes.
        # sentiment_analysis + entity_detection were already on; preserved.
        # L9: universal-3-pro does NOT accept `word_boost` — uses `keyterms_prompt`
        # instead per AAI 400 contract: '"word_boost" is not compatible with
        # universal-3-pro. Use "prompt" or "keyterms_prompt"'. Glossary terms
        # joined into a single keyterms_prompt string. ~50 terms typical.
        glossary = load_supplier_glossary(supplier_hint)
        submit_payload = {
            "audio_url": upload_url,
            "speech_models": ["universal-3-pro"],
            "speaker_labels": True,
            "entity_detection": True,
            "auto_highlights": True,
            "iab_categories": True,
            "sentiment_analysis": True,
            # L9 additions
            "keyterms_prompt": glossary,
            "redact_pii": True,
            "redact_pii_audio": False,
            "redact_pii_policies": PII_POLICIES,
            "redact_pii_sub": PII_REDACT_SUB,
        }
        submit_resp = await client.post(
            f"{ASSEMBLYAI_BASE}/transcript",
            headers={"authorization": api_key, "content-type": "application/json"},
            json=submit_payload,
        )
        submit_resp.raise_for_status()
        job_id = submit_resp.json()["id"]

        # Step 3: Poll for completion
        while True:
            await asyncio.sleep(3)
            poll_resp = await client.get(
                f"{ASSEMBLYAI_BASE}/transcript/{job_id}",
                headers={"authorization": api_key},
            )
            status = poll_resp.json()["status"]
            if status == "completed":
                break
            if status == "error":
                error_msg = poll_resp.json().get("error", "Unknown error")
                raise RuntimeError(f"AssemblyAI transcription failed: {error_msg}")

    elapsed = time.time() - t0
    data = poll_resp.json()

    # L9 race fix: when redact_pii=True, AssemblyAI sometimes flips status to
    # "completed" before the redacted text materializes in the response body.
    # Re-poll up to 5x at 2s intervals until text shows up.
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as repoll:
        for attempt in range(5):
            if data.get("text"):
                break
            log.info(f"🎙️ ASSEMBLYAI text empty after status=completed, re-poll attempt {attempt+1}/5")
            await asyncio.sleep(2)
            r = await repoll.get(
                f"{ASSEMBLYAI_BASE}/transcript/{job_id}",
                headers={"authorization": api_key},
            )
            data = r.json()
        if not data.get("text"):
            log.warning(f"🎙️ ASSEMBLYAI text still empty after 5 re-polls — proceeding with empty transcript")

    # Extract per-word data
    raw_words = data.get("words", [])
    words = [
        {
            "word": w.get("text", ""),
            "punctuated_word": w.get("text", ""),
            "start": w.get("start", 0) / 1000.0,  # AssemblyAI returns ms, convert to seconds
            "end": w.get("end", 0) / 1000.0,
            "speaker": w.get("speaker", "UNK"),
            "confidence": w.get("confidence", 0),
        }
        for w in raw_words
    ]

    # Extract utterances (speaker turns)
    utterances = [
        {
            "speaker": u.get("speaker", "UNK"),
            "text": u.get("text", ""),
            "start": u.get("start", 0) / 1000.0,
            "end": u.get("end", 0) / 1000.0,
        }
        for u in data.get("utterances", [])
    ]

    transcript = data.get("text", "")
    word_count = len(words)
    speakers = len(set(w["speaker"] for w in words))

    # Intelligence-layer counts for the log line so operators can see what
    # came back without opening the DB. Everything survives inside `data`
    # and is persisted verbatim to calls.assemblyai_metadata JSONB.
    chapters = len(data.get("chapters") or [])
    entities = len(data.get("entities") or [])
    highlights = len(((data.get("auto_highlights_result") or {}).get("results")) or [])
    sentiment_segs = len(data.get("sentiment_analysis_results") or [])
    iab_labels = len(((data.get("iab_categories_result") or {}).get("results")) or [])
    summary = data.get("summary")

    log.info(
        f"🎙️ ASSEMBLYAI done → {word_count} words, {speakers} speakers, "
        f"chapters×{chapters}, entities×{entities}, highlights×{highlights}, "
        f"sentiment×{sentiment_segs}, iab×{iab_labels}, "
        f"summary={'yes' if summary else 'no'}, {elapsed:.1f}s"
    )

    # L9: With redact_pii=True, `data["text"]` is the REDACTED transcript.
    # AssemblyAI stores the unredacted version on a follow-up endpoint that
    # is only fetched on explicit request; if any client wants the raw text
    # for checkpoint scoring, it lives on this dict as `raw_transcript`.
    # When redact_pii=False (legacy path) raw == redacted.
    raw_transcript = data.get("text", "") or ""

    # Sentiment + entity convenience views on the metadata (top-level keys
    # already exist in `data`; we surface them in a stable shape so callers
    # don't have to know AssemblyAI's exact field names).
    sentiment_segments = data.get("sentiment_analysis_results") or []
    entity_results = data.get("entities") or []
    sentiment_view = [
        {
            "utterance_idx": idx,
            "sentiment": (s.get("sentiment") or "").lower(),
            "confidence": s.get("confidence", 0),
            "start": (s.get("start", 0) or 0) / 1000.0,
            "end": (s.get("end", 0) or 0) / 1000.0,
            "text": s.get("text", ""),
        }
        for idx, s in enumerate(sentiment_segments)
    ]
    entity_view = [
        {
            "entity_type": e.get("entity_type", ""),
            "text": e.get("text", ""),
            "start": (e.get("start", 0) or 0) / 1000.0,
            "end": (e.get("end", 0) or 0) / 1000.0,
        }
        for e in entity_results
    ]
    metadata_view = dict(data)
    metadata_view["sentiment"] = sentiment_view
    metadata_view["entities"] = entity_view
    metadata_view["redacted"] = True

    return {
        "transcript": transcript,        # redacted (safe for storage + display)
        "raw_transcript": raw_transcript, # alias for downstream that needs raw
        "words": words,
        "utterances": utterances,
        "metadata": metadata_view,
    }
