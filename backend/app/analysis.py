import json

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

DETECT_PROMPT = """You are analyzing a call from an energy brokerage. The agent works for a broker/TPI (third-party intermediary) and is offering or discussing a deal from a specific energy SUPPLIER.

Your job: identify which energy SUPPLIER's deal is being offered or discussed in this call. The supplier is NOT the broker — it's the energy company whose contract/tariff is being sold.

Look for clues like:
- Direct mentions of supplier names
- References to tariffs, contracts, or deals from a specific supplier
- "We can put you on [supplier]" or "the rates from [supplier]"
- Supplier names may appear anywhere in the call, not just the beginning

Known suppliers: British Gas, E.ON Next, Scottish Power, EDF, Pozitive, BGL, Npower, SSE, Opus Energy, Haven Power, Total Energies

FULL TRANSCRIPT:
{transcript_start}

Respond with ONLY the supplier name. If you truly cannot identify any supplier after reading the entire transcript, respond "Unknown"."""

DETECT_SCRIPT_PROMPT = """Based on this call transcript, which specific script type best matches this call?

The supplier is: {supplier}

Available scripts for this supplier:
{script_options}

TRANSCRIPT (first 800 words):
{transcript_start}

Consider the call context: Is this a new customer acquisition, a renewal, an upgrade? Is it about gas or electricity? Single site or multi-site?

Respond with ONLY the number of the best matching script (e.g., "1" or "2"). If truly uncertain, respond "1"."""


DETECT_CALL_TYPE_PROMPT = """You are categorising a recorded UK energy-brokerage compliance call (Watt Utilities / TPI). Classify this transcript into exactly ONE of the six Ofgem-TPI lifecycle stages.

The six categories (always lowercase snake_case) and their distinguishing signals:

1. lead_gen
   The FIRST contact. Cold/warm intro. Agent introduces themselves and the
   brokerage, qualifies interest, captures site / decision-maker / current
   supplier / contract end date. NO verbal contract. NO LOA wording.
   Signals: "is that [name]?", "I'm calling from [broker]", "are you the
   decision maker", "your current contract", "shall I send across some
   prices", "I'll pass you to my colleague who handles pricing".

2. passover
   A WARM HANDOVER between two agents on the same call. Lead-gen agent
   introduces a SECOND named agent (the closer) to the same customer, then
   the second agent picks up. No legally-binding verbal contract reading.
   Signals: "I'll just pass you over to [name] who's our pricing manager",
   "hi [customer], my colleague [name] tells me…", two distinct agent
   voices introducing each other inside the same recording.

3. closer
   The LEGALLY BINDING VERBAL CONTRACT. Closer agent reads the supplier's
   verbatim script: contract length, unit rate, standing charge, VAT/CCL,
   cooling-off, Ombudsman, "do you accept?" or "yes I agree". For E.ON
   variants, the LOA wording is bundled INTO this same call. For full-call
   recordings that cover Lead Gen → Passover → Closer in one go, pick
   `closer` (the closer content dominates the audit-relevant content).
   Signals: explicit rate p/kWh + standing charge + contract length, "this
   is a legally binding contract", "do you agree to be bound", customer
   says "yes" to contractual affirmation blocks.

4. standalone_loa
   A SEPARATE, dedicated Letter of Authority call. Required by every
   supplier EXCEPT E.ON. Confirms customer authorises Watt to act on their
   behalf with the supplier (data access, termination, objection, billing).
   Pure LOA content with NO verbal contract rate reading.
   Signals: "I'm calling for a Letter of Authority", "do you authorise
   Watt to act on your behalf", "this gives us 12 months", "authority to
   negotiate with [supplier]", no rate/contract-length reading.

5. c_call
   A CONFIRMATION CALLBACK after the main sale — sometimes by the supplier,
   sometimes by Watt. Short, verifies the customer's identity + that they
   knowingly entered the contract.
   Signals: "calling to confirm you've signed up with [supplier]", "can you
   confirm your name and the business address", "I just need to verify a
   few details from your earlier call".

6. amendment
   A POST-SALE FIX-UP call to correct a specific mistake on a prior verbal
   or LOA (rate misread, name correction, missing legal line). Always
   references the earlier call.
   Signals: "we noticed on your earlier call", "I need to re-read lines
   11 to 14", "to amend the verbal contract you took yesterday".

CRITICAL RULES:
- Pick exactly ONE category. Do not invent new ones.
- If a recording covers multiple stages but ONE dominates the audit content,
  pick the dominant one (full bundled E.ON-style intake → `closer`).
- If you genuinely cannot tell, pick `lead_gen` (least-binding default).
- Output ONLY the snake_case label on a single line. No JSON, no
  explanation, no punctuation, no quotes.

TRANSCRIPT (first 2500 words):
{transcript_start}

Answer:"""


DETECT_NAMES_PROMPT = """Read the start of this energy brokerage call transcript and extract two names.

The transcript is labeled with `Agent:` and `Customer:` per turn (a broker
calls a business owner about their energy contract). The AGENT is the
person from the brokerage (says "my name is …", "calling from …",
"your electricity supply"). The CUSTOMER is the person on the line who
owns or runs the business.

EXTRACT:
1. AGENT name — the broker / sales agent's OWN name (the speaker tagged
   `Agent:` who says "my name is X" or whose first name follows that
   pattern). NEVER the customer's name.
2. CUSTOMER name — the person tagged `Customer:`. Often introduced when
   the agent says "speaking with [name]?" and the customer confirms, or
   when the customer self-identifies. NEVER the agent's name.

CRITICAL RULES:
- The two names MUST be different people. If you only see one name, put
  it on the line that matches the speaker tag and "Unknown" on the other.
- If the transcript shows "Agent: hi jay my name is paris" then
  AGENT=Paris and CUSTOMER=Jay (the agent is greeting the customer by
  name then introducing themselves).
- Full name if given, first name if only first given.
- "Unknown" if truly unclear.
- Do NOT include titles (Mr, Mrs) or company names.
- Do NOT mix up customer ↔ agent. Re-read the speaker tags before answering.

TRANSCRIPT START:
{transcript_start}

Respond with ONLY a single line in this exact format (no JSON, no prose):
AGENT: <name or Unknown>
CUSTOMER: <name or Unknown>"""


PROVIDERS = ("openrouter", "gemini", "anthropic", "openai")


@LLM_RETRY
async def _call_llm(
    prompt: str,
    timeout: float = 60.0,
    system: str | None = None,
) -> str:
    """Provider-agnostic LLM call.

    `system` is an optional cacheable prefix. Anthropic uses it as a system
    block with `cache_control: ephemeral` so identical prefixes are billed at
    ~10% on subsequent calls. Other providers receive `system + "\n\n" + prompt`
    as a single user message.
    """
    provider = settings.active_provider
    dispatch = {
        "openrouter": _call_openrouter,
        "gemini": _call_gemini,
        "anthropic": _call_anthropic,
        "openai": _call_openai,
    }
    fn = dispatch.get(provider, _call_openrouter)
    if provider == "anthropic":
        return await fn(prompt, timeout, system=system)
    if system:
        prompt = f"{system}\n\n{prompt}"
    return await fn(prompt, timeout)


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


async def _call_openrouter(prompt: str, timeout: float) -> str:
    return await _call_openai_compat(
        "https://openrouter.ai/api/v1/chat/completions",
        settings.openrouter_api_key, settings.openrouter_model,
        prompt, timeout, "OpenRouter",
    )


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


async def detect_supplier(transcript: str) -> str:
    # Send full transcript (up to 3000 words) — supplier name can appear anywhere
    words = transcript.split()
    transcript_text = " ".join(words[:3000])
    prompt = DETECT_PROMPT.replace("{transcript_start}", transcript_text)
    result = await _call_llm(prompt, timeout=30.0)
    detected = result.strip().strip('"')
    log.info(f"\U0001f50d DETECT supplier \u2192 \"{detected}\"")
    return detected


# Canonical lifecycle stage codes. MUST match
# `app/deal_lifecycle.py:_CALL_TYPE_TO_PHASE` keys + frontend
# CallType enum exactly.
_VALID_CALL_TYPES: frozenset[str] = frozenset(
    {"lead_gen", "passover", "closer", "standalone_loa", "c_call", "amendment"}
)


async def detect_call_type(transcript: str) -> str | None:
    """Classify a recording's lifecycle stage from the transcript.

    Returns one of the six canonical codes, or ``None`` if the LLM
    failed / answered with anything unrecognised. Callers fall through
    to ``call_type="full"`` in that case (the lifecycle resolver treats
    ``full`` as Lead Gen + Passover + Closer for the E.ON bundled flow).

    No filename inputs. No regex. Pure transcript classification \u2014
    the explicit product requirement (2026-05-11) to replace the
    previous weak filename pre-pass with a content-aware AI call.
    """
    if not transcript or len(transcript.strip()) < 50:
        return None

    words = transcript.split()
    transcript_start = " ".join(words[:2500])
    prompt = DETECT_CALL_TYPE_PROMPT.replace("{transcript_start}", transcript_start)
    try:
        raw = await _call_llm(prompt, timeout=20.0)
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


async def detect_names(transcript: str) -> tuple[str, str]:
    """Extract (agent_name, customer_name) from the start of a transcript.

    Single cheap LLM call; returns ("Unknown", "Unknown") on any failure.
    """
    words = transcript.split()
    transcript_start = " ".join(words[:600])
    prompt = DETECT_NAMES_PROMPT.replace("{transcript_start}", transcript_start)
    try:
        result = await _call_llm(prompt, timeout=20.0)
    except Exception as e:
        log.warning(f"\U0001f464 DETECT names failed: {e}")
        return "Unknown", "Unknown"

    agent = "Unknown"
    customer = "Unknown"
    for line in result.splitlines():
        line = line.strip()
        if line.upper().startswith("AGENT:"):
            agent = line.split(":", 1)[1].strip().strip('"') or "Unknown"
        elif line.upper().startswith("CUSTOMER:"):
            customer = line.split(":", 1)[1].strip().strip('"') or "Unknown"

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
