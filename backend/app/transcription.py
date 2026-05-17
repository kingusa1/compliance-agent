import base64
import json
import os
import re

import httpx
from deepgram import DeepgramClient, DeepgramClientOptions, PrerecordedOptions

from app.config import settings
from app.logger import log
from app.resilience import DEEPGRAM_RETRY


# UK National Insurance number — Deepgram does not redact NI natively, so we
# strip them after transcription. Pattern: 2 letters (excluding D, F, I, Q, U, V
# in first position; D, F, I, O, Q, U, V in second) + 6 digits + 1 of A-D,
# optionally space-separated (e.g. "AB 12 34 56 C").
_UK_NI_RE = re.compile(
    r"\b[A-CEGHJ-PR-TW-Z][A-CEGHJ-NPR-TW-Z]\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b",
    re.IGNORECASE,
)


def redact_uk_ni(text: str) -> str:
    """Replace any UK NI numbers in `text` with [REDACTED-NI]."""
    if not text:
        return text
    return _UK_NI_RE.sub("[REDACTED-NI]", text)


def _get_deepgram_client() -> DeepgramClient:
    # Pin to EU endpoint for UK call PII residency. Configurable via
    # DEEPGRAM_BASE_URL env in case a different region is required.
    config = DeepgramClientOptions(url=settings.deepgram_base_url)
    return DeepgramClient(settings.deepgram_api_key, config)


def _format_timestamp(seconds: float) -> str:
    total_seconds = int(seconds)
    minutes = total_seconds // 60
    secs = total_seconds % 60
    return f"{minutes:02d}:{secs:02d}"


def _detect_agent_speaker(words: list[dict]) -> str:
    """Heuristic-pick which speaker key (Deepgram int or AssemblyAI letter)
    is the broker / agent.

    The static "speaker_0 = Agent" rule misfires whenever the customer is
    the one who picks up first ("hello?") — Deepgram assigns speaker 0 to
    whoever speaks first, not to the caller.

    Strategy: build a per-speaker word bag and score each speaker on
    broker-side phrasing. The speaker whose text most strongly resembles
    sales / disclosure language is the agent. Pure regex / counts so this
    runs offline (no extra LLM call on the hot path).

    2026-05-18 generalisation: the original signature returned ``int`` because
    Deepgram emits numeric speaker ids. AssemblyAI emits letters ("A", "B"),
    and the diarization selector now writes AAI's word list to
    ``call.word_data`` when AAI wins. Coercing "A" via ``int()`` raised
    ``ValueError``, the except branch fell to 0, every word landed on speaker
    0, and the transcript player rendered the whole call as one AGENT turn.
    Speaker keys are now strings throughout; callers compare via ``str(spk)``.
    """
    if not words:
        return "0"

    bags: dict[str, list[str]] = {}
    for w in words:
        raw = w.get("speaker")
        if raw is None or raw == "":
            continue
        spk = str(raw)
        if spk in {"UNK", "unknown"}:
            continue
        bags.setdefault(spk, []).append(
            str(w.get("word") or w.get("text") or "").lower()
        )

    if len(bags) < 2:
        return next(iter(bags), "0")

    agent_signals = (
        # Self-introductions and broker-side framing.
        "my name is", "i'm calling from", "calling from", "third party",
        # Energy broker domain language — only the broker says these.
        "your electricity supply", "your gas supply", "your energy supply",
        "your current contract", "your current supplier",
        "renewal", "best price", "cheapest price", "quote you",
        "i'll transfer", "transfer your call", "pricing manager",
        "decision maker", "letter of authority", "loa",
        "standing charge", "kwh", "p/kwh", "tariff", "fixed for",
        "are you the decision", "are you the business owner",
        "we work with", "we're a broker", "broker", "intermediary",
        # Suppliers — only the broker name-drops these.
        "british gas", "scottish power", "edf", "eon", "e.on", "npower",
        "pozitive", "bgl", "british gas lite",
    )

    scores: dict[str, int] = {}
    for spk, tokens in bags.items():
        text = " ".join(tokens)
        scores[spk] = sum(1 for s in agent_signals if s in text)

    best = max(scores, key=scores.get)
    if scores[best] == 0:
        # No signal — fall back to "speaker who talks more is the agent"
        # (brokers carry the call ~3:1 in our corpus).
        best = max(bags, key=lambda k: len(bags[k]))
    return best


def format_diarized_transcript(words: list[dict]) -> str:
    if not words:
        return ""

    # Speaker keys are stringified so Deepgram int ids ("0"/"1") and
    # AssemblyAI letters ("A"/"B") flow through the same comparison path.
    # See _detect_agent_speaker for the 2026-05-18 generalisation.
    agent_speaker = _detect_agent_speaker(words)

    lines = []
    current_speaker = None
    current_text = []
    current_start = 0.0

    for word_info in words:
        raw = word_info.get("speaker")
        speaker = "" if raw is None or raw == "" else str(raw)
        word = word_info.get("word") or word_info.get("text") or ""
        start = word_info.get("start", 0.0)

        if speaker != current_speaker:
            if current_text:
                label = "Agent" if current_speaker == agent_speaker else "Customer"
                timestamp = _format_timestamp(current_start)
                lines.append(f"[{timestamp}] {label}: {' '.join(current_text)}")
            current_speaker = speaker
            current_text = [word]
            current_start = start
        else:
            current_text.append(word)

    if current_text:
        label = "Agent" if current_speaker == agent_speaker else "Customer"
        timestamp = _format_timestamp(current_start)
        lines.append(f"[{timestamp}] {label}: {' '.join(current_text)}")

    return "\n".join(lines)


@DEEPGRAM_RETRY
async def _call_deepgram(client: DeepgramClient, source: dict, options: PrerecordedOptions):
    return await client.listen.asyncrest.v("1").transcribe_file(source, options)


async def transcribe_audio(file_path: str) -> str:
    client = _get_deepgram_client()

    with open(file_path, "rb") as audio:
        source = {"buffer": audio.read()}

    options = PrerecordedOptions(
        model="nova-3",
        diarize=True,
        punctuate=True,
        smart_format=True,
        language=settings.deepgram_language,
        # PII redaction at source — covers credit cards, generic ID numbers,
        # and SSN-shaped tokens. UK National Insurance numbers are not in
        # Deepgram's redaction set, so we strip them with redact_uk_ni below.
        redact=["pci", "numbers", "ssn"],
    )

    log.info("\U0001f399\ufe0f DEEPGRAM calling Nova-3 with diarization (en-GB, redaction on)...")
    response = await _call_deepgram(client, source, options)
    words = response.results.channels[0].alternatives[0].words

    word_dicts = [
        {
            "word": w.word if hasattr(w, "word") else w["word"],
            "speaker": w.speaker if hasattr(w, "speaker") else w.get("speaker", 0),
            "start": w.start if hasattr(w, "start") else w.get("start", 0),
            "end": w.end if hasattr(w, "end") else w.get("end", 0),
        }
        for w in words
    ]

    log.info(f"\U0001f399\ufe0f DEEPGRAM done \u2192 {len(word_dicts)} words transcribed")

    transcript = format_diarized_transcript(word_dicts)
    return redact_uk_ni(transcript)


async def transcribe_audio_full(file_path: str) -> dict:
    """Transcribe with Deepgram Nova-3 and capture every intelligence signal.

    Returns:
        {
          "transcript": str,   # diarized markdown (Agent: / Customer: lines)
          "words":      list,  # per-word dicts for word_data column
          "metadata":   dict,  # full provider response for deepgram_metadata
        }

    The `metadata` bag includes per-word confidence (already on `words`),
    per-segment sentiment, intent classification, topic detection, and an
    auto-generated summary. All requested on the same call — no extra
    round-trip — so the marginal cost is a few tenths of a cent per call.
    """
    client = _get_deepgram_client()

    with open(file_path, "rb") as audio:
        source = {"buffer": audio.read()}

    options = PrerecordedOptions(
        model="nova-3",
        diarize=True,
        punctuate=True,
        smart_format=True,
        # Intelligence layer — what Nova-3 returns for free (topics, intents,
        # summary) plus sentiment which is a small per-minute surcharge.
        sentiment=True,
        intents=True,
        topics=True,
        summarize="v2",
        # UK English locale — Nova-3 supports en-GB and sentiment requires
        # an explicit language pin.
        language=settings.deepgram_language,
        # PII redaction at source. UK National Insurance numbers are added
        # via redact_uk_ni() post-process since Deepgram does not include NI.
        redact=["pci", "numbers", "ssn"],
    )

    log.info("\U0001f399\ufe0f DEEPGRAM Nova-3 — diarize + sentiment + intents + topics + summary")
    response = await _call_deepgram(client, source, options)
    words = response.results.channels[0].alternatives[0].words

    word_dicts = [
        {
            "word": w.word if hasattr(w, "word") else w["word"],
            "punctuated_word": (w.punctuated_word if hasattr(w, "punctuated_word") else w.get("punctuated_word", "")),
            "speaker": w.speaker if hasattr(w, "speaker") else w.get("speaker", 0),
            "start": w.start if hasattr(w, "start") else w.get("start", 0),
            "end": w.end if hasattr(w, "end") else w.get("end", 0),
            "confidence": w.confidence if hasattr(w, "confidence") else w.get("confidence", 0),
        }
        for w in words
    ]

    transcript = format_diarized_transcript(word_dicts)

    # Serialise the full response so the pipeline can persist it verbatim.
    # The SDK returns a nested response object; dict-stringify walks the whole
    # tree. Fall back to an empty dict on any serialisation failure so a
    # Deepgram SDK shape change doesn't block the pipeline.
    try:
        metadata = response.to_dict() if hasattr(response, "to_dict") else json.loads(response.to_json())
    except Exception as err:
        log.warning(f"DEEPGRAM metadata serialisation failed: {err}")
        metadata = {}

    # Log what intelligence came back so operators can see it in terminal
    # without opening the DB. Counts only — don't flood the log with content.
    sentiment_segs = len(((metadata.get("results") or {}).get("sentiments") or {}).get("segments") or [])
    intents_segs = len(((metadata.get("results") or {}).get("intents") or {}).get("segments") or [])
    topics_segs = len(((metadata.get("results") or {}).get("topics") or {}).get("segments") or [])
    has_summary = bool(((metadata.get("results") or {}).get("summary") or {}).get("short"))
    log.info(
        f"\U0001f399\ufe0f DEEPGRAM done \u2192 {len(word_dicts)} words, "
        f"sentiment\u00d7{sentiment_segs}, intents\u00d7{intents_segs}, "
        f"topics\u00d7{topics_segs}, summary={'yes' if has_summary else 'no'}"
    )

    return {
        "transcript": redact_uk_ni(transcript),
        "words": word_dicts,
        "metadata": metadata,
    }


GEMINI_TRANSCRIBE_PROMPT = """Transcribe this audio exactly word for word. This is a compliance call between an energy broker agent and a customer.

Rules:
- Include EVERY word spoken, including filler words (um, uh, yeah, mmhmm)
- Label each line with the speaker: [Agent] or [Customer]
- Include timestamps in [MM:SS] format at the start of each speaker turn
- Do not skip or summarize any section
- Preserve exact wording — do not paraphrase or clean up grammar
- If you can't make out a word, write [inaudible]"""


async def transcribe_audio_gemini(file_path: str) -> str:
    """Transcribe audio using Gemini 2.5 Flash via OpenRouter (higher accuracy)."""
    with open(file_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    ext = os.path.splitext(file_path)[1].lstrip(".")
    fmt = ext if ext in ("mp3", "wav", "m4a", "ogg", "flac") else "mp3"

    log.info(f"\U0001f916 GEMINI transcribing {fmt} audio...")

    async with httpx.AsyncClient(timeout=180.0) as client:
        response = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "google/gemini-2.5-flash",
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": GEMINI_TRANSCRIBE_PROMPT},
                        {"type": "input_audio", "input_audio": {"data": b64, "format": fmt}},
                    ],
                }],
                "temperature": 0,
                "max_tokens": 16384,
            },
        )
        response.raise_for_status()

    data = response.json()
    transcript = data["choices"][0]["message"]["content"]
    cost = data.get("usage", {}).get("cost", 0)
    log.info(f"\U0001f916 GEMINI done \u2192 {len(transcript.split())} words, ${cost:.4f}")

    return transcript
