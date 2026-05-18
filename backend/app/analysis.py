import json
import re

import httpx

from app.watt_compliance.phrase_regex import PhraseHit, hit_summary, scan as phrase_scan
from app.watt_compliance.prompts import system_prompt_for_call_type
from app.watt_compliance.risk_tags import normalize_risk_tags
from app.watt_compliance.script_detect import DetectionResult, detect as script_detect
from app.watt_compliance.taxonomy import Severity, VerdictAction
from app.config import settings
from app.logger import log
from app.resilience import LLM_RETRY
from app.schemas import ComplianceResult, CheckpointComplianceResult, CheckpointResult, RuleCheckpoint

V1_PROMPT = """You are a compliance analyst for an energy brokerage.

Analyze the following call transcript and determine if the agent complied with this rule:

RULE: THIRD_PARTY_DISCLOSURE
Checkpoints to evaluate individually:
1. The agent explicitly states the company is a third party
2. The agent states the company is NOT an energy supplier (e.g., British Gas, E.ON Next, Scottish Power)
3. The agent identifies themselves/company as an independent broker or intermediary

TRANSCRIPT:
{transcript}

Respond ONLY with valid JSON, no other text:
{{
  "compliant": true or false,
  "reason": "Brief explanation of the overall verdict",
  "excerpt": "The most relevant quote from the transcript",
  "agent_name": "The agent's name if mentioned in the call, otherwise 'Unknown'",
  "customer_name": "The customer's name if mentioned in the call, otherwise 'Unknown'",
  "checkpoints": [
    {{ "rule": "The agent explicitly states the company is a third party", "passed": true or false, "excerpt": "exact quote from the transcript proving this, or the section where it should have occurred", "notes": "1-2 sentence plain-English explanation. For pass: WHY the wording satisfies the rule. For fail: WHAT was expected and what the agent said instead." }},
    {{ "rule": "The agent states the company is NOT an energy supplier", "passed": true or false, "excerpt": "exact quote from the transcript proving this, or the section where it should have occurred", "notes": "1-2 sentence plain-English explanation. Always populated." }},
    {{ "rule": "The agent identifies themselves/company as an independent broker or intermediary", "passed": true or false, "excerpt": "exact quote from the transcript proving this, or the section where it should have occurred", "notes": "1-2 sentence plain-English explanation. Always populated." }}
  ]
}}

If compliant: all checkpoints passed, reason should confirm what the agent said correctly.
If non-compliant: reason should explain which checkpoints were missed and why."""

# V2 prompt is split into a static system block (cacheable) and a dynamic user
# block. The Anthropic provider sends the system block with cache_control so
# every call after the first hits the prompt cache (≈90% off on the prefix).
# Other providers receive the concatenated V2_PROMPT for backwards-compat.
V2_SYSTEM_TEMPLATE = """You are a compliance auditor. You must check a call transcript against a supplier script.

MODE: {mode}
- word_for_word: Agent must say phrases closely matching the script text. Minor natural speech variations allowed (um, uh, filler words) but the core wording must match.
- meaning_for_meaning: Agent must convey the same information/meaning as each section but can use their own words.

Each checkpoint also has a per-checkpoint STRICTNESS level that takes precedence over the global mode:
- verbatim: Check for near-exact wording match — the agent must use the prescribed phrases very closely
- mandatory: Check that the information was conveyed in any natural language — exact wording not required
- customer_yes: Verify BOTH the agent statement AND an affirmative customer response (e.g., "Yes", "Yeah", "That's fine")

The user message will contain SCRIPT CHECKPOINTS followed by the call TRANSCRIPT.

RULES:
1. Check EACH checkpoint against the transcript
2. For each checkpoint, apply the strictness level indicated for that checkpoint
3. If FOUND: status "pass", quote the EXACT words from the transcript (no paraphrasing)
4. If PARTIALLY FOUND: status "partial", quote what was said and explain what was missing
5. If NOT FOUND: status "fail", evidence must be "NOT FOUND IN TRANSCRIPT"
6. You are FORBIDDEN from inventing or paraphrasing quotes. Only use exact text from the transcript.
7. For customer_yes checkpoints: fail if customer confirmation is absent, even if agent said the right words
8. Also extract: agent_name, customer_name, detected_supplier

Respond ONLY with valid JSON, no other text:
{{
  "detected_supplier": "supplier name from the transcript",
  "agent_name": "agent name or Unknown",
  "customer_name": "customer name or Unknown",
  "mode": "{mode}",
  "checkpoints": [
    {{
      "section": 1,
      "name": "section name",
      "status": "pass or partial or fail",
      "evidence": "exact quote from transcript or NOT FOUND IN TRANSCRIPT",
      "notes": "what was missing (for partial/fail only, null for pass)"
    }}
  ],
  "summary": {{
    "total": 0,
    "passed": 0,
    "partial": 0,
    "failed": 0,
    "compliant": true or false,
    "score": "X/Y"
  }}
}}"""

V2_USER_TEMPLATE = """SCRIPT CHECKPOINTS:
{checkpoints_text}

TRANSCRIPT:
{transcript}"""

# Legacy single-prompt form kept for non-Anthropic providers and any external
# callers that still concatenate everything client-side.
V2_PROMPT = V2_SYSTEM_TEMPLATE + "\n\n" + V2_USER_TEMPLATE

DETECT_PROMPT = """You are analyzing a UK energy-broker compliance call (Watt Utilities / TPI). The agent works for a broker and is selling a contract on behalf of ONE specific energy SUPPLIER.

YOUR JOB
Identify the SUPPLIER the agent is selling FOR in THIS call — the supplier
whose contract / rate / verbal script the agent will read out (or did read
out). Many calls also mention OTHER suppliers in passing (the customer's
current supplier they're leaving, prior supplier history, comparison
quotes). Those are RED HERRINGS — pick only the broker's TARGET supplier.

HOW TO TELL THEM APART
The broker's target supplier is the one tied to phrases like:
  • "calling on behalf of <SUPPLIER>"
  • "not directly employed by <SUPPLIER>"  (verbal contract preamble)
  • "your new contract is with <SUPPLIER>"
  • "you've agreed a 24-month plan with <SUPPLIER>"
  • "<SUPPLIER> are planning to change your meter"
  • the verbal-contract script comes from <SUPPLIER>'s template

The customer's CURRENT (departing) supplier is referenced via:
  • "how have you found everything with <SUPPLIER>?"
  • "your contract with <SUPPLIER> is ending"
  • "we'll let <SUPPLIER> know"
  • "are you happy to leave <SUPPLIER>?"
Those are NOT the target supplier.

KNOWN SUPPLIERS (canonical labels — return exactly as written when matched)
  British Gas
  E.ON Next
  Scottish Power
  EDF
  Pozitive
  BGL
  Npower
  SSE
  Opus Energy
  Haven Power
  Total Energies
  Drax
  SmartestEnergy
  Octopus

If the transcript names a supplier outside this list, still output the
canonical form they used.

FULL TRANSCRIPT:
{transcript_start}

Respond with ONLY the supplier name on a single line. If after reading
the entire transcript you genuinely cannot identify the broker's target
supplier, output exactly: Unknown"""

DETECT_SCRIPT_PROMPT = """Based on this call transcript, which specific script type best matches this call?

The supplier is: {supplier}

Available scripts for this supplier:
{script_options}

TRANSCRIPT (first 800 words):
{transcript_start}

Consider the call context: Is this a new customer acquisition, a renewal, an upgrade? Is it about gas or electricity? Single site or multi-site?

Respond with ONLY the number of the best matching script (e.g., "1" or "2"). If truly uncertain, respond "1"."""


DETECT_CALL_TYPE_PROMPT = """You are categorising a recorded UK energy-brokerage compliance call (Watt Utilities / TPI). Classify this transcript by the DOMINANT segment present. Pick exactly ONE of these four categories.

The four categories (always lowercase snake_case) and their distinguishing signals:

1. lead_gen
   The FIRST contact recording, taken by the lead-generation agent.
   Cold/warm intro to a customer who's never spoken to Watt before.
   Agent introduces themselves and Watt, qualifies the customer, captures
   decision-maker + current supplier + contract end date.
   NO verbal contract reading. NO LOA wording.
   Signals: "is that [name]?", "I'm calling from Watt Utilities", "are
   you the decision maker", "your current contract", "shall I send across
   some prices", "I'll pass you to my colleague who handles pricing".

2. pre_sales
   The WARM-UP at the START of the closer call. A second (closer) agent
   re-introduces themselves to the customer after the lead-gen handover,
   re-confirms identity + authority, and prepares for the verbal contract.
   No legally-binding script reading yet — that's the "verbal" segment.
   Signals: "thanks for taking my colleague's call, I'm [name] from
   Watt", "let me re-confirm a few details", "are you still the decision
   maker", "before we start the recording for the contract".

3. verbal
   The LEGALLY BINDING VERBAL CONTRACT reading. Closer agent reads the
   supplier's verbatim verbal-contract script — contract length, unit
   rate, standing charge, VAT/CCL, cooling-off, Ombudsman, with explicit
   customer "yes / I agree" responses to affirmation blocks.
   Signals: explicit rate p/kWh + standing charge + contract length,
   "this is a legally binding contract", "do you agree to be bound",
   "is that correct?" with customer "yes" responses.

4. loa
   The LETTER OF AUTHORITY wording. Customer authorises Watt to act on
   their behalf with the supplier (data access, termination, objection,
   billing). For E.ON this is bundled inside the closer recording; for
   other suppliers LOA is on paper/DocuSign and rarely appears in audio.
   Signals: "do you authorise Watt to act on your behalf", "letter of
   authority", "this gives us 12 months", "authority to negotiate with
   [supplier]", "to obtain information about your account".

CRITICAL RULES:
- Pick exactly ONE of: lead_gen, pre_sales, verbal, loa. No others.
- This is the DOMINANT-segment classification only. If a recording
  contains multiple segments stitched together (very common for closer
  recordings: pre_sales + verbal + loa in one file), pick the segment
  that takes the MOST minutes of audio. A downstream content-classifier
  agent will identify per-segment boundaries separately.
- If you genuinely cannot tell, pick `lead_gen` (least-binding default).
- Output ONLY the snake_case label on a single line. No JSON, no
  explanation, no punctuation, no quotes.

TRANSCRIPT (first 2500 words):
{transcript_start}

Answer:"""


DETECT_NAMES_PROMPT = """Read the start of this energy brokerage call transcript and extract two names.

A broker calls a business owner about their energy contract. Transcripts
may or may not have `Agent:` / `Customer:` speaker labels — they are
sometimes a single un-labelled paragraph. Use ALL textual cues to decide
who is who.

EXTRACT:
1. AGENT name — the broker / sales agent's OWN first (+ surname if given)
   name. Look for self-introduction phrases the agent uses:
     • "my name is X" / "my name's X"
     • "this is X calling/speaking"
     • "I'm X from …" / "I am X"
     • "you're through to X" / "you're speaking with X"
     • "X here from …"
   The agent is also the one who says "calls are recorded for monitoring",
   "third party intermediary", "act on your behalf", "your supplier".
2. CUSTOMER name — the person who OWNS or RUNS the business. Often
   introduced by the agent ("speaking with X?", "is that X?") and confirmed
   by the customer, or self-identified in response to "please confirm
   your name".

CRITICAL RULES:
- The two names MUST be different people. If only one name appears, fill
  the matching slot and put "Unknown" on the other.
- Names can be ANY length, ANY cultural origin, and may LOOK UNUSUAL or
  MISSPELLED (e.g. "Afak", "Parat", "Aaqib") because of transcription
  drift — accept them as names anyway. Do NOT reject as "Unknown" just
  because a name looks unfamiliar.
- A name token contains only letters, hyphens and apostrophes. Reject
  generic words ("calling", "speaking", "here", "third", "party").
- Full name if given (first + surname); first name alone if that's all
  that's said.
- "Unknown" ONLY if truly unclear — never as a hedge.
- Do NOT include titles (Mr, Mrs, Dr) or company names.
- Re-read the transcript before answering. Names usually appear in the
  first 30 seconds.

TRANSCRIPT START:
{transcript_start}

Respond with ONLY two lines in this exact format (no JSON, no prose, no
markdown):
AGENT: <name or Unknown>
CUSTOMER: <name or Unknown>"""


PROVIDERS = ("openrouter", "gemini", "anthropic", "openai")


@LLM_RETRY
async def _call_llm(
    prompt: str,
    timeout: float = 60.0,
    system: str | None = None,
    *,
    cheap: bool = False,
) -> str:
    """Provider-agnostic LLM call.

    Args:
        prompt:   user message.
        timeout:  request timeout in seconds.
        system:   optional cacheable prefix. Anthropic uses it as a system
                  block with ``cache_control: ephemeral`` so identical prefixes
                  are billed at ~10% on subsequent calls. Other providers
                  receive ``system + "\\n\\n" + prompt`` as a single user
                  message.
        cheap:    route this call to the cheaper model (Sonnet 4.6 instead of
                  Opus 4.7 on OpenRouter). Use for high-volume / low-judgment
                  tasks: name extraction, supplier detection, business-name
                  extraction, call-type classification, date extraction. Keep
                  False for the LLM calls that drive compliance grading
                  (content_classifier, checkpoint_analyzer) where Opus's
                  judgment is worth the cost. Per Aly 2026-05-15 ask: "use
                  Sonnet and Opus next to each other to weigh the price."
    """
    provider = settings.active_provider
    if provider == "anthropic":
        # Anthropic direct API path doesn't route by `cheap` today — it
        # always uses settings.anthropic_model. Most prod traffic flows
        # through OpenRouter so this is fine.
        return await _call_anthropic(prompt, timeout, system=system)
    if provider == "openrouter":
        # 2026-05-16 — OpenRouter forwards Anthropic-style `cache_control`
        # markers on Claude models. `_call_openrouter` decides at runtime
        # whether to use the cacheable-prefix payload shape (Claude model
        # + system >=3500 chars) or fall back to the legacy concatenation.
        return await _call_openrouter(prompt, timeout, cheap=cheap, system=system)
    # Gemini / OpenAI paths — no native Anthropic cache; fold system into prompt.
    if system:
        prompt = f"{system}\n\n{prompt}"
    if provider == "gemini":
        return await _call_gemini(prompt, timeout)
    return await _call_openai(prompt, timeout)


def _strip_code_fences(content: str) -> str:
    if content.startswith("```"):
        content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return content


async def _call_openai_compat(url: str, api_key: str, model: str, prompt: str, timeout: float, label: str) -> str:
    """Shared caller for OpenAI-compatible APIs (OpenRouter, OpenAI, Anthropic messages)."""
    log.info(f"\U0001f916 LLM [{label}] calling {model} (timeout={timeout}s)")
    async with httpx.AsyncClient() as client:
        response = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "max_tokens": 4096,
            },
            timeout=timeout,
        )
        response.raise_for_status()

    data = response.json()
    content = data["choices"][0]["message"]["content"].strip()
    content = _strip_code_fences(content)
    log.info(f"\U0001f916 LLM [{label}] response \u2192 {len(content)} chars")
    return content


async def _call_openrouter(
    prompt: str,
    timeout: float,
    *,
    cheap: bool = False,
    system: str | None = None,
) -> str:
    """OpenRouter transport with optional Anthropic-style prompt caching.

    When ``system`` is provided, the target model is a Claude model, and
    the system block is >= 3500 chars (~1024 tokens — Anthropic's caching
    minimum), we send the request in OpenRouter's Anthropic-flavoured
    shape with ``cache_control: ephemeral`` on the system content block.
    OpenRouter forwards the flag to Anthropic; cached reads bill at ~10%
    of input rate for 5 minutes after the first call.

    Falls back to the legacy ``system + "\\n\\n" + prompt`` concatenation
    when caching is not eligible (non-Claude model, sub-threshold system,
    or no system block at all).
    """
    model = settings.openrouter_cheap_model if cheap else settings.openrouter_model
    label = "OpenRouter/cheap" if cheap else "OpenRouter"
    is_claude = "anthropic/claude" in (model or "").lower()
    cache_eligible = (
        system is not None and is_claude and len(system) >= 3500
    )
    if cache_eligible:
        return await _call_openrouter_cached(
            prompt=prompt,
            system=system,  # type: ignore[arg-type]  # narrowed by cache_eligible
            model=model,
            timeout=timeout,
            label=f"{label}+cache",
        )
    # Legacy non-cached path — fold system into prompt for parity with
    # OpenAI / non-Anthropic providers.
    if system:
        prompt = f"{system}\n\n{prompt}"
    return await _call_openai_compat(
        "https://openrouter.ai/api/v1/chat/completions",
        settings.openrouter_api_key, model,
        prompt, timeout, label,
    )


async def _call_openrouter_cached(
    *,
    prompt: str,
    system: str,
    model: str,
    timeout: float,
    label: str,
) -> str:
    """OpenRouter request with an Anthropic-style cacheable system block.

    Mirrors the payload shape of ``_call_anthropic`` but routes through
    OpenRouter (so existing OPENROUTER_API_KEY + model id keep working).
    OpenRouter requires the system content to be an array with explicit
    ``type: text`` content blocks for ``cache_control`` to take effect.
    """
    log.info(f"\U0001f916 LLM [{label}] calling {model} (timeout={timeout}s)")
    payload: dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "system": [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "temperature": 0,
        "max_tokens": 4096,
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()
    data = response.json()
    content = data["choices"][0]["message"]["content"].strip()
    content = _strip_code_fences(content)
    # OpenRouter forwards Anthropic's prompt-cache usage counters when
    # they're present; missing keys mean the cache flag wasn't honoured
    # (older Claude models, or OpenRouter dropping the flag).
    usage = data.get("usage") or {}
    cache_read = (
        usage.get("cache_read_input_tokens")
        or usage.get("prompt_tokens_details", {}).get("cached_tokens")
        or 0
    )
    cache_write = (
        usage.get("cache_creation_input_tokens")
        or 0
    )
    log.info(
        f"\U0001f916 LLM [{label}] response → {len(content)} chars "
        f"(cache_read={cache_read}, cache_write={cache_write})"
    )
    return content


async def _call_openai(prompt: str, timeout: float) -> str:
    return await _call_openai_compat(
        "https://api.openai.com/v1/chat/completions",
        settings.openai_api_key, settings.openai_model,
        prompt, timeout, "OpenAI",
    )


async def _call_anthropic(
    prompt: str,
    timeout: float,
    system: str | None = None,
) -> str:
    """Direct Anthropic Messages API call with optional prompt-cached system block.

    When `system` is provided and >=1024 tokens (~3.5 KB), it is sent as a
    system block tagged `cache_control: ephemeral`. Anthropic caches the prefix
    for 5 minutes so repeated calls with the same `system` are billed at ~10%
    of input rate on the cached portion.
    """
    cache_eligible = bool(system) and len(system) >= 3500  # ~1024 tokens
    payload: dict = {
        "model": settings.anthropic_model,
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
    }
    if system:
        if cache_eligible:
            payload["system"] = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        else:
            payload["system"] = system

    label = "Anthropic+cache" if cache_eligible else "Anthropic"
    log.info(f"\U0001f916 LLM [{label}] calling {settings.anthropic_model} (timeout={timeout}s)")
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": settings.anthropic_api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()

    data = response.json()
    content = data["content"][0]["text"].strip()
    content = _strip_code_fences(content)
    usage = data.get("usage", {})
    cache_hit = usage.get("cache_read_input_tokens", 0)
    cache_write = usage.get("cache_creation_input_tokens", 0)
    log.info(
        f"\U0001f916 LLM [{label}] response \u2192 {len(content)} chars "
        f"(cache_read={cache_hit}, cache_write={cache_write})"
    )
    return content


async def _call_gemini(prompt: str, timeout: float) -> str:
    model = settings.gemini_model
    log.info(f"\U0001f916 LLM [Gemini] calling {model} (timeout={timeout}s)")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    async with httpx.AsyncClient() as client:
        response = await client.post(
            url,
            headers={
                "Content-Type": "application/json",
                "X-goog-api-key": settings.gemini_api_key,
            },
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0},
            },
            timeout=timeout,
        )
        response.raise_for_status()

    data = response.json()
    content = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    content = _strip_code_fences(content)
    log.info(f"\U0001f916 LLM [Gemini] response \u2192 {len(content)} chars")
    return content


# 2026-05-18 Westbury audit: ASR/Deepgram occasionally drops the dot/space
# in multi-word supplier names ("eonext" / "britishgas") and the LLM returns
# "Unknown" even when the broker clearly named the target supplier nearby.
# A deterministic regex pre-pass catches the unambiguous cases (broker target
# context: "on behalf of <X>", "<X> energy supply", "agreed with <X>")
# before we spend a full Opus call. More specific patterns first so
# "e.on next" beats "e.on" / "eon" for the same window.
_SUPPLIER_REGEX_PREPASS: tuple[tuple["re.Pattern[str]", str], ...] = (
    (re.compile(r"\b(?:e\s*[.\-]?\s*on\s*next|eonext)\b", re.IGNORECASE), "E.ON Next"),
    (re.compile(r"\bbritish\s*gas\s*lite\b", re.IGNORECASE), "BGL"),
    (re.compile(r"\bbritish\s*gas\b", re.IGNORECASE), "British Gas"),
    (re.compile(r"\bscottish\s*power\b", re.IGNORECASE), "Scottish Power"),
    (re.compile(r"\bpozitive\b", re.IGNORECASE), "Pozitive"),
    (re.compile(r"\bedf\b", re.IGNORECASE), "EDF"),
    (re.compile(r"\bsmartest\b", re.IGNORECASE), "SmartestEnergy"),
    (re.compile(r"\bopus\s+energy\b", re.IGNORECASE), "Opus Energy"),
    (re.compile(r"\b(?:total\s+energies|totalenergies)\b", re.IGNORECASE), "Total Energies"),
    (re.compile(r"\be\s*[.\-]?\s*on\b", re.IGNORECASE), "E.ON"),
)


def _supplier_regex_prepass(transcript: str) -> str | None:
    """Return a canonical supplier label when the transcript names a known
    supplier in a broker-target context, else None.

    Distinguishes broker target supplier (the contract the agent is selling)
    from the customer's departing supplier by requiring a target-cue phrase
    near the supplier mention.
    """
    if not transcript:
        return None
    # Scan only the opening 2000 chars; the broker-target supplier is named
    # in the third-party disclosure / verbal preamble, both early in the call.
    head = transcript[:2000].lower()
    target_cues = (
        "on behalf of", "your contract is with", "your new contract is with",
        "agreed with", "energy supply", "not directly employed by",
        "the contract with", "from the contract with", "supply at",
    )
    for pattern, canonical in _SUPPLIER_REGEX_PREPASS:
        m = pattern.search(head)
        if not m:
            continue
        win_start = max(0, m.start() - 120)
        win_end = min(len(head), m.end() + 120)
        window = head[win_start:win_end]
        if any(cue in window for cue in target_cues):
            return canonical
    return None


async def detect_supplier(transcript: str) -> str:
    # 2026-05-18: deterministic regex pre-pass for the common spelled-together
    # ASR variants ("eonext"). When it hits, skip the LLM entirely.
    prepass = _supplier_regex_prepass(transcript)
    if prepass:
        log.info(f"\U0001f50d DETECT supplier regex pre-pass -> {prepass!r}")
        return prepass
    # Send full transcript (up to 3000 words)— supplier name can appear anywhere
    words = transcript.split()
    transcript_text = " ".join(words[:3000])
    prompt = DETECT_PROMPT.replace("{transcript_start}", transcript_text)
    # 2026-05-16 — Mohamed mandate: use Opus 4.7 across the board.
    # Sonnet was returning unreliable supplier picks on noisy transcripts;
    # accuracy > cost for this classifier.
    result = await _call_llm(prompt, timeout=30.0, cheap=False)
    detected = result.strip().strip('"')
    log.info(f"\U0001f50d DETECT supplier \u2192 \"{detected}\"")
    return detected


# Canonical lifecycle stage codes. MUST match
# `app/deal_lifecycle.py:_CALL_TYPE_TO_PHASE` keys + frontend
# CallType enum exactly.
#
# 2026-05-12 taxonomy rebuild \u2014 locked to 4 values only. The old
# {passover, closer, standalone_loa, c_call, amendment, full, verbal}
# vocabulary is GONE. New uploads must classify to one of these four,
# and a separate content_classifier agent identifies per-segment
# boundaries inside the recording for fine-grained grading.
_VALID_CALL_TYPES: frozenset[str] = frozenset(
    {"lead_gen", "pre_sales", "verbal", "loa"}
)


async def detect_call_type(transcript: str) -> str | None:
    """Classify a recording's DOMINANT lifecycle stage from the transcript.

    Returns one of the four canonical codes, or ``None`` if the LLM
    failed / answered with anything unrecognised. Callers fall back to
    leaving ``call.call_type`` unset and let the content_classifier do
    the per-segment work.

    No filename inputs. Pure transcript classification.
    """
    if not transcript or len(transcript.strip()) < 50:
        return None

    words = transcript.split()
    transcript_start = " ".join(words[:2500])
    prompt = DETECT_CALL_TYPE_PROMPT.replace("{transcript_start}", transcript_start)
    try:
        # 2026-05-16 — Opus 4.7 mandate from Mohamed: Sonnet was misclassifying
        # call_type on noisy transcripts. Accuracy > cost.
        raw = await _call_llm(prompt, timeout=20.0, cheap=False)
    except Exception as e:
        log.warning(f"\U0001f3af DETECT call_type LLM failed: {e}")
        return None

    candidate = (
        raw.strip()
        .strip('"')
        .strip("'")
        .strip(".")
        .splitlines()[0]
        .strip()
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )
    if candidate in _VALID_CALL_TYPES:
        log.info(f"\U0001f3af DETECT call_type \u2192 \"{candidate}\"")
        return candidate
    log.warning(
        f"\U0001f3af DETECT call_type returned unrecognised value: \"{raw[:80]}\""
    )
    return None


# 2026-05-14 \u2014 deterministic agent-name extractor.
#
# Background: a call transcript "...my name is afak and we are third party
# intermediate seaway utilities..." was getting agent_name=Unknown because
# the LLM rejected "afak" as a name (unusual transliteration + the prompt
# expected `Agent:` / `Customer:` speaker labels that aren't actually in the
# transcript). This regex layer catches the canonical TPI self-introduction
# phrases before we even call the LLM \u2014 and overrides the LLM if the LLM
# returns Unknown / a different name for an obvious self-intro.
#
# Patterns are deliberately conservative \u2014 we only capture the first name
# token after the trigger phrase (no greedy multi-word match) to avoid
# slurping in "afak and we are..." \u2192 "afak and we are". Surname capture is
# attempted separately when the next token is also a candidate name (not
# in the stopword set).
_AGENT_INTRO_TRIGGERS = re.compile(
    r"\b(?:"
    r"my\s+name(?:\s+is|\'s|s)?"
    r"|this\s+is"
    r"|i\s*am"
    r"|i\'?m"
    r"|you'?re\s+through\s+to"
    r"|you'?ve\s+come\s+through\s+to"
    r"|come\s+through\s+to"
    r"|you'?re\s+speaking\s+(?:to|with)"
    r"|speaking\s+(?:to|with)"
    r")\s+([A-Za-z][A-Za-z\-']{1,25})(?:\s+([A-Za-z][A-Za-z\-']{1,25}))?",
    re.IGNORECASE,
)

# Reserved for "it's/it is X" — too noisy on its own ("it's bounced back",
# "it is going fine") so we only accept it when followed by a name-confirming
# pivot word ("here", "from", "speaking", "calling"). Caught the bogus
# agent_name="Bounced" extraction on 2026-05-15.
_IT_IS_AGENT_INTRO = re.compile(
    r"\b(?:it'?s|it\s+is)\s+"
    r"([A-Za-z][A-Za-z\-']{1,25})(?:\s+([A-Za-z][A-Za-z\-']{1,25}))?\s+"
    r"(?:here|from|speaking|calling|on\s+the\s+line)\b",
    re.IGNORECASE,
)

# Secondary pattern: name FIRST then a self-intro trigger.
# Catches "sarah here from watt utilities", "jay speaking from …".
_AGENT_TRAILING_TRIGGER = re.compile(
    r"\b([A-Za-z][A-Za-z\-']{1,25})(?:\s+([A-Za-z][A-Za-z\-']{1,25}))?\s+"
    r"(?:here\s+from|speaking\s+from|calling\s+from)\b",
    re.IGNORECASE,
)

# Words that look like name candidates by shape but aren't real names.
# These appear right after self-intro triggers in mis-transcribed calls
# ("my name's calling you from", "this is regarding\u2026") and need filtering
# so we don't write "Calling" or "Regarding" into call.agent_name.
_NAME_STOPWORDS = frozenset(
    {
        "calling", "speaking", "here", "with", "from", "at", "for", "the",
        "your", "you", "and", "but", "today", "yes", "no", "hi", "hello",
        "ok", "okay", "perfect", "great", "good", "thanks", "thank",
        "afternoon", "morning", "evening", "sir", "ma'am", "madam",
        "going", "regarding", "checking", "ringing", "phoning", "back",
        "about", "just", "actually", "obviously", "really", "very",
        "third", "party", "intermediate", "intermediary", "broker",
        "brokerage", "agent", "representative", "rep", "manager",
        "company", "limited", "ltd", "incorporated", "supplier",
        "utilities", "energy", "gas", "electric", "electricity",
        "british", "scottish", "next", "eon", "totalenergies", "edf",
        "drax", "smartest", "octopus", "pozitive", "watt",
        # Words mis-captured as names when the previous regex was greedy
        # on "it's X" / "it is X" — kept defensively even though the
        # trigger is now gated. Found 2026-05-15.
        "bounced", "fine", "alright", "almost", "absolutely", "anyway",
        "definitely", "exactly", "literally", "probably", "basically",
        "honestly", "frankly", "still", "already", "always", "never",
        "pricing", "verification", "compliance", "account", "renewal",
        "deemed", "contract", "voicemail", "answering",
        "team", "department", "colleague", "buddy", "boss", "guys",
        "mate", "love", "darling", "pal", "bro", "dude",
        # Common UK supplier/customer noise after "this is" / "it's"
        "an", "a", "another", "one", "two", "three", "four", "five",
    }
)


def _title_case_name(raw: str) -> str:
    """Normalize a name token to Display Case. 'afak' \u2192 'Afak'; 'mcdonald'
    \u2192 'Mcdonald' (good enough \u2014 surname normalisation is downstream's job).
    """
    return " ".join(w[:1].upper() + w[1:].lower() for w in raw.split() if w)


# PII redaction tokens emitted by Deepgram / AssemblyAI when their `redact`
# feature fires — e.g. "[PERSON_NAME]", "[PHONE_NUMBER]", "[date_1]". The
# extractor (regex or LLM) occasionally captures these verbatim, which used
# to land in `Call.customer_name` / `Call.agent_name` as literal "[PERSON_NAME]"
# strings (2026-05-18 audit, Crosby Grange call). This regex matches a
# single redaction token; `_strip_pii_tokens` collapses to "Unknown" when the
# whole value is a token and strips inline tokens otherwise.
_PII_TOKEN_RE = re.compile(r"\[[a-zA-Z][a-zA-Z_]*(?:_\d+)?\]")


def _strip_pii_tokens(name: str | None) -> str:
    """Return ``name`` with any PII redaction tokens removed.

    Returns "Unknown" when the value is empty, None, or collapses to empty
    after token removal (the literal "[PERSON_NAME]" case). Tokens that are
    embedded inside a real name are stripped in-place ("[PERSON_NAME] Doe"
    → "Doe").
    """
    if not name:
        return "Unknown"
    cleaned = _PII_TOKEN_RE.sub("", name).strip().strip(",.;:'\"-").strip()
    return cleaned or "Unknown"


def _extract_agent_name_regex(transcript: str) -> str | None:
    """Return the first plausible agent-name candidate from canonical TPI
    self-introduction phrases in the transcript head, else None.

    Scans only the first 1500 chars because TPI agents always self-introduce
    in the opening seconds \u2014 running on the full transcript would catch
    other names (customer, brokerage staff, third parties) and we'd lose
    determinism. Returns the title-cased first name, optionally with a
    surname when the second captured token is also name-shaped.
    """
    head = transcript[:1500].replace("\n", " ")
    # Try the strict triggers first, then the gated "it's X here/from/…" pattern.
    for regex in (_AGENT_INTRO_TRIGGERS, _IT_IS_AGENT_INTRO):
        for m in regex.finditer(head):
            first = (m.group(1) or "").strip("'\"-,.;: ")
            second = (m.group(2) or "").strip("'\"-,.;: ") if m.group(2) else ""
            if not first:
                continue
            if first.lower() in _NAME_STOPWORDS:
                continue
            if not (2 <= len(first) <= 25):
                continue
            # Skip if the captured value is a PII redaction token like
            # "[PERSON_NAME]" — the brackets keep \w-class out so this is
            # defence-in-depth in case the regex grammar drifts.
            if _PII_TOKEN_RE.fullmatch(first or "") or _PII_TOKEN_RE.fullmatch(second or ""):
                continue
            # Accept the surname only when it looks like a name (not a stopword,
            # alphabetic, plausible length).
            if second and 2 <= len(second) <= 25 and second.lower() not in _NAME_STOPWORDS:
                return _title_case_name(f"{first} {second}")
            return _title_case_name(first)
    return None


# Job-title-shaped tokens that occasionally appear in mis-transcribed
# interlocutor cues like "speaking to Art Engineer". When the LLM's agent
# answer contains one of these, prefer the regex pre-pass result (which only
# fires on canonical self-introductions).
_AGENT_NAME_JOB_TITLE_STOPS: frozenset[str] = frozenset(
    {
        "engineer", "manager", "director", "supervisor", "representative",
        "advisor", "adviser", "specialist", "consultant", "executive",
        "officer", "assistant", "coordinator", "analyst", "operator",
        "operative", "associate", "trainee",
    }
)


def _llm_agent_smells_fabricated(name: str | None) -> bool:
    """Return True when the LLM's agent answer looks like a glued-together
    interlocutor cue ("Art Engineer") rather than a real self-intro."""
    if not name or name == "Unknown":
        return False
    tokens = [t.strip().lower() for t in name.split() if t.strip()]
    return any(t in _AGENT_NAME_JOB_TITLE_STOPS for t in tokens)


async def detect_names(transcript: str) -> tuple[str, str]:
    """Extract (agent_name, customer_name) from a call transcript.

    Two-layer extraction (2026-05-14):

      Layer 1 \u2014 Regex pre-pass on canonical TPI self-introduction phrases
                ("my name is X", "this is X", "I'm X", "you're through to X").
                Bulletproof for the common cases the LLM has gotten wrong on
                unusual or mis-transcribed names (Afak / Parat / etc).
      Layer 2 \u2014 LLM call for nuance (resolves customer name and any case
                the regex missed). The regex result wins on conflict for
                the AGENT slot only; CUSTOMER stays with the LLM.

    Returns ("Unknown", "Unknown") only when BOTH layers fail.
    """
    regex_agent = _extract_agent_name_regex(transcript)
    if regex_agent:
        log.info(f"\U0001f464 DETECT names regex pre-pass \u2192 agent={regex_agent!r}")

    words = transcript.split()
    transcript_start = " ".join(words[:600])
    prompt = DETECT_NAMES_PROMPT.replace("{transcript_start}", transcript_start)
    try:
        # 2026-05-16 — Opus 4.7 mandate from Mohamed: name extraction is
        # downstream of every grading decision, so accuracy > cost.
        result = await _call_llm(prompt, timeout=20.0, cheap=False)
    except Exception as e:
        log.warning(f"\U0001f464 DETECT names LLM failed: {e}")
        # Regex still wins for the agent when the LLM is down.
        return (regex_agent or "Unknown"), "Unknown"

    agent = "Unknown"
    customer = "Unknown"
    for line in result.splitlines():
        line = line.strip()
        if line.upper().startswith("AGENT:"):
            agent = line.split(":", 1)[1].strip().strip('"') or "Unknown"
        elif line.upper().startswith("CUSTOMER:"):
            customer = line.split(":", 1)[1].strip().strip('"') or "Unknown"

    # \u2500\u2500 Regex fallback on the AGENT slot \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    # The LLM gets full-context resolution (surname, normalisation, dedup
    # against customer); we only let regex win when the LLM returned
    # Unknown. This stops the regex from overriding a high-quality LLM
    # answer with a partial first-name-only match.
    if regex_agent and agent == "Unknown":
        log.info(
            f"\U0001f464 DETECT names regex fallback \u2192 "
            f"agent={regex_agent!r} (LLM said Unknown)"
        )
        agent = regex_agent
    elif regex_agent and _llm_agent_smells_fabricated(agent):
        # 2026-05-18 Westbury audit: the LLM picked "Art Engineer" off a
        # mis-transcribed interlocutor cue ("speaking to Art Engineer
        # [PERSON_NAME]") and overwrote the regex's high-confidence
        # "James" capture from "i am james calling from watt utilities".
        # When the LLM's output is multi-word with a job-title-shaped
        # token (engineer / manager / advisor / etc.), prefer the regex
        # capture \u2014 the regex only fires on canonical self-intro phrases
        # so its false-positive rate is bounded by _NAME_STOPWORDS.
        log.warning(
            f"\U0001f464 DETECT names regex preference \u2192 "
            f"agent={regex_agent!r} (LLM gave job-title-shaped {agent!r})"
        )
        agent = regex_agent

    # Sanity: if agent and customer collapsed to the same name (LLM
    # confusion), trust the customer (which gets cross-validated downstream
    # against the Customer table) and clear the agent so the reviewer
    # doesn't see a misleading attribution.
    if (
        agent != "Unknown"
        and customer != "Unknown"
        and agent.strip().lower() == customer.strip().lower()
    ):
        log.warning(
            f"\u26a0\ufe0f DETECT names collision \u2192 agent=customer=\"{agent}\"; clearing agent"
        )
        agent = "Unknown"

    # PII redaction tokens (e.g. "[PERSON_NAME]", "[date_1]") sometimes get
    # captured verbatim by the LLM when the transcript was redacted at the
    # transcription layer. Sanitize before returning so the persistence
    # layer never sees a literal token in either name field.
    agent = _strip_pii_tokens(agent)
    customer = _strip_pii_tokens(customer)

    log.info(f"\U0001f464 DETECT names \u2192 agent=\"{agent}\", customer=\"{customer}\"")
    return agent, customer


async def detect_script_variant(transcript: str, supplier: str, scripts: list[dict]) -> int:
    """Pick the best script variant when a supplier has multiple scripts.

    Args:
        transcript: Full call transcript
        supplier: Detected supplier name
        scripts: List of dicts with 'index' (0-based), 'id', 'script_name'

    Returns:
        0-based index into the scripts list
    """
    if len(scripts) <= 1:
        return 0

    words = transcript.split()
    transcript_start = " ".join(words[:800])

    options = "\n".join(
        f"  {i + 1}. {s['script_name']}" for i, s in enumerate(scripts)
    )

    prompt = DETECT_SCRIPT_PROMPT.replace("{supplier}", supplier)
    prompt = prompt.replace("{script_options}", options)
    prompt = prompt.replace("{transcript_start}", transcript_start)

    result = await _call_llm(prompt, timeout=15.0)
    choice = result.strip().strip('"').strip('.')

    try:
        idx = int(choice) - 1
        if 0 <= idx < len(scripts):
            log.info(f"\U0001f3af SCRIPT VARIANT \u2192 picked #{idx + 1}: \"{scripts[idx]['script_name']}\" (out of {len(scripts)} options)")
            return idx
    except ValueError:
        pass

    log.warning(f"\u26a0\ufe0f SCRIPT VARIANT \u2192 couldn't parse \"{result}\", defaulting to first script")
    return 0


async def analyze_compliance_v1(transcript: str) -> ComplianceResult:
    prompt = V1_PROMPT.replace("{transcript}", transcript)
    content = await _call_llm(prompt)
    parsed = json.loads(content)
    checkpoints = [RuleCheckpoint(**cp) for cp in parsed.pop("checkpoints", [])]
    return ComplianceResult(**parsed, checkpoints=checkpoints)


async def analyze_compliance_v2(
    transcript: str,
    checkpoints: list[dict],
    mode: str = "meaning_for_meaning",
) -> CheckpointComplianceResult:
    checkpoints_text = ""
    for cp in checkpoints:
        checkpoints_text += f"\nCHECKPOINT {cp['section']}: {cp['name']}\n"
        checkpoints_text += f"  Required: {cp['required']}\n"
        checkpoints_text += f"  Key phrases: {', '.join(cp.get('key_phrases', []))}\n"
        checkpoints_text += f"  Customer response required: {cp.get('customer_response_required', False)}\n"
        checkpoints_text += f"  Strictness: {cp.get('strictness', 'mandatory')}\n"

    # Static system block (rules + JSON schema + mode definitions) is identical
    # across every call for a given mode, so it caches cleanly. Dynamic per-call
    # data (checkpoints + transcript) goes in the user message.
    system = V2_SYSTEM_TEMPLATE.replace("{mode}", mode)
    user_prompt = V2_USER_TEMPLATE.replace("{checkpoints_text}", checkpoints_text)
    user_prompt = user_prompt.replace("{transcript}", transcript)

    content = await _call_llm(user_prompt, timeout=90.0, system=system)
    parsed = json.loads(content)

    checkpoint_results = [CheckpointResult(**cp) for cp in parsed["checkpoints"]]

    return CheckpointComplianceResult(
        detected_supplier=parsed.get("detected_supplier", "Unknown"),
        agent_name=parsed.get("agent_name", "Unknown"),
        customer_name=parsed.get("customer_name", "Unknown"),
        mode=mode,
        checkpoints=checkpoint_results,
        summary=parsed.get("summary", {}),
    )


# ─── Phase 2 — Watt-grounded analysis ──────────────────────────────────────
#
# `analyze_compliance_watt` is the production analysis path described in
# `.planning/phase2-analysis/PHASE2-PLAN.md`. It:
#
#   1. Auto-detects supplier / call_class / script_type from the transcript
#      (deterministic regex layer, no LLM cost).
#   2. Runs the cheap regex pre-pass (`phrase_regex.scan`) to seed the LLM
#      with high-confidence Critical hits and to short-circuit the verdict
#      when the regex layer alone is decisive.
#   3. Calls the LLM with the Watt-grounded system prompt (8 Standards,
#      27 rejection reasons, 4 master categories, severity → action mapping,
#      operations-team fix_required tone).
#   4. Returns the parsed JSON dict for the caller (typically the pipeline
#      step in `app/workflows/process_call.py` and the rejection_factory).
#
# Wiring: `app/checkpoint_analyzer.py` will route through this function when
# `settings.use_watt_prompt` is True (added below). Existing v1/v2 callers
# remain untouched for backwards compatibility with current tests.

async def analyze_compliance_watt(
    transcript: str,
    *,
    call_type: str | None = None,
    supplier_hint: str | None = None,
    script_chunks: list[str] | None = None,
) -> dict:
    """Run a full Watt-grounded compliance audit on a transcript.

    Returns the LLM's parsed JSON object (verdict / score / rejections /
    risk_tags / summary / supplier_detected / call_type_detected). The
    auto-detected supplier is included alongside the LLM's own detection
    so callers can spot disagreements.
    """
    detection: DetectionResult = script_detect(transcript)
    effective_supplier = supplier_hint or (
        detection.supplier.value if detection.supplier else None
    )

    # Cheap regex pre-pass — runs on EVERY call, costs nothing.
    hits: list[PhraseHit] = phrase_scan(
        transcript, call_type=call_type, supplier=effective_supplier
    )
    summary = hit_summary(hits)

    # Format the regex evidence for the LLM context. The LLM still owns the
    # final verdict; we just hand it the candidate hits as a hint.
    if hits:
        regex_block = "\n## Pre-pass regex evidence\n\n" + "\n".join(
            f"- [{h.severity.value}] {h.rule_id} ({h.reason.code} — {h.reason.title}): "
            f'{h.why} {"matched: " + repr(h.matched_text) if h.matched_text else "(absence)"}'
            for h in hits
        )
    else:
        regex_block = "\n## Pre-pass regex evidence\n\n(none — no automatic flags)"

    detection_block = (
        f"\n## Detected metadata\n\n"
        f"- supplier: {detection.supplier.value if detection.supplier else 'unknown'}\n"
        f"- script_type: {detection.script_type.value if detection.script_type else 'unknown'}\n"
        f"- call_class: {detection.call_class.value if detection.call_class else 'unknown'}\n"
        f"- caller-supplied call_type: {call_type or 'unspecified'}\n"
    )

    script_block = ""
    if script_chunks:
        script_block = "\n## Supplier script chunks (RAG-retrieved)\n\n" + "\n\n---\n\n".join(
            f"[chunk {i + 1}/{len(script_chunks)}]\n{c}" for i, c in enumerate(script_chunks)
        )

    user_message = (
        f"## Transcript\n\n{transcript}\n"
        f"{detection_block}"
        f"{regex_block}"
        f"{script_block}\n\n"
        "Respond with the strict JSON object defined in the system prompt."
    )

    system = system_prompt_for_call_type(call_type)
    content = await _call_llm(user_message, timeout=120.0, system=system)
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        # Defend against malformed LLM output. We log the raw content and
        # surface a degraded but well-typed response so the pipeline does
        # not crash on a single bad call.
        log.warning("watt_analysis_json_decode_failed", extra={"raw": content[:500]})
        parsed = {
            "verdict": "REVIEW",
            "score": 50,
            "compliance_status": "non_compliant",
            "rejections": [],
            "risk_tags": [],
            "summary": "LLM output could not be parsed; manual review required.",
            "supplier_detected": effective_supplier,
            "call_type_detected": call_type,
        }

    # Stitch in the regex pre-pass + script-detect output so downstream code
    # has the full evidence chain in one dict.
    parsed.setdefault("supplier_detected", effective_supplier)
    # Coerce LLM-supplied risk_tags to the canonical 4 (drop unknowns silently;
    # spec defines exactly ombudsman / mis_selling / complaint / cancellation).
    parsed["risk_tags"] = normalize_risk_tags(parsed.get("risk_tags") or [])
    parsed["regex_pre_pass"] = {
        "hits": [
            {
                "rule_id": h.rule_id,
                "reason_code": h.reason.code,
                "severity": h.severity.value,
                "matched_text": h.matched_text,
                "why": h.why,
                "span": list(h.span),
            }
            for h in hits
        ],
        "summary": summary,
    }
    parsed["auto_detected"] = {
        "supplier": detection.supplier.value if detection.supplier else None,
        "script_type": detection.script_type.value if detection.script_type else None,
        "call_class": detection.call_class.value if detection.call_class else None,
    }

    # Auto-escalate: a single CRITICAL regex hit forces the verdict to BLOCK
    # regardless of what the LLM said. The LLM's own assessment is preserved
    # under `llm_verdict` for audit.
    if summary.get(Severity.CRITICAL.value, 0) > 0 and parsed.get("verdict") not in {"BLOCK"}:
        parsed["llm_verdict"] = parsed.get("verdict")
        parsed["verdict"] = VerdictAction.BLOCK.value
        parsed.setdefault("escalation_reason",
                          "regex_pre_pass detected at least one CRITICAL hit")

    return parsed
