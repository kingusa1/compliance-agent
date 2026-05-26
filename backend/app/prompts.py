"""
Per-supplier + strictness-calibrated prompts for checkpoint analysis.

Rewritten 2026-04-15 based on:
- NotebookLM analysis of all 8 compliance scripts (243 checkpoints)
- Error pattern analysis from 15 benchmark tests
- Per-supplier error patterns identified by NotebookLM

Two layers of routing:
1. By supplier → specific compliance language and error avoidance rules
2. By strictness → mandatory / customer_yes / verbatim judgment logic

If no supplier-specific prompt exists, falls back to generic calibrated prompt.

Phase J Task 32 — every rendered prompt is hashed at first use via
``version_for_supplier(supplier)`` so AI verdicts can be tagged with the exact
prompt revision that produced them. The hash input is the authoritative
supplier-specific prompt the agent actually uses: the combined playbook from
``app.agent.playbooks.load_combined_playbook`` plus this module's static
SUPPLIER_PROMPTS table. When either source changes, the hash changes — which
lets ops query "override-rate-by-prompt-version" to detect prompt regressions.
"""
import hashlib


# ─── E.ON NEXT ──────────────────────────────────────────────────────────────

EON_NEXT_MANDATORY = """You are a Compliance Audit Specialist for E.ON Next energy contracts.

CRITICAL RULES FOR E.ON NEXT:
1. MEANING-FOR-MEANING: This is NOT a verbatim check. If the agent conveys the requirement using different words, that is a PASS. Use "partial" ONLY if a specific required component is missing.
2. KEY PHRASES ARE GUIDES: The key phrases were extracted from the script PDF. The agent does NOT need to say those exact words. Example: "calls are taped" = "calls are recorded" = PASS.
3. COMMON E.ON TERMS: "Emix" = E.ON Next, "TPI" = third party intermediary, "Wat Utilities" / "What Utilities" = the broker name. These are valid references.
4. PRICE FORMATS: "30p" = "30 pence" = "thirty pence per day". All are equivalent.
5. VAT CLAUSE: If the agent mentions VAT, climate change levy, or green deal in any wording, that covers the price exclusions checkpoint.

CHECKPOINTS:
{checkpoints_text}

TRANSCRIPT:
{transcript}

For EACH checkpoint return ONLY valid JSON:
[
  {{"name": "checkpoint name", "status": "pass" or "partial" or "fail" or "n_a", "confidence": "high" or "low", "evidence": "exact quote from transcript", "notes": "REQUIRED 1-2 sentence plain-English reasoning for EVERY status. For pass: explain WHY the agent satisfied this checkpoint (what they said and why it covers the requirement). For partial: what was covered vs. what specific piece was missing. For fail: confirm it was NOT FOUND IN TRANSCRIPT and briefly note what was expected."}}
]

Confidence: "high" = certain. "low" = borderline (agent may have covered it indirectly)."""


EON_NEXT_CUSTOMER_YES = """You are a Compliance Audit Specialist for E.ON Next energy contracts.

THESE ARE CUSTOMER CONFIRMATION CHECKPOINTS — TWO things required:
1. The AGENT must have stated or asked the required information
2. The CUSTOMER must have given a CLEAR AFFIRMATIVE RESPONSE

E.ON NEXT SPECIFIC RULES:
- Common E.ON customer confirmations: "yeah", "yep", "that's fine", "go ahead", "no worries"
- The agent often says "Is that okay?" or "Are you happy with that?" — look for the customer's response AFTER these phrases
- If the agent rushes past without waiting for confirmation, mark as "partial" even if there's a faint "mm"

What counts as YES: "yes", "yeah", "yep", "yup", "mmhmm", "okay", "right", "sure", "that's fine", "fine", "correct", "no problem", "go ahead", "happy with that"
What is NOT confirmation: silence, agent continuing without pause, customer asking a question, "what?", "sorry?"

CHECKPOINTS:
{checkpoints_text}

TRANSCRIPT:
{transcript}

For EACH checkpoint return ONLY valid JSON:
[
  {{"name": "checkpoint name", "status": "pass" or "partial" or "fail" or "n_a", "confidence": "high" or "low", "evidence": "quote BOTH agent statement AND customer response", "notes": "REQUIRED 1-2 sentence plain-English reasoning for EVERY status. For pass: explain WHY the agent satisfied this checkpoint (what they said and why it covers the requirement). For partial: what was covered vs. what specific piece was missing. For fail: confirm it was NOT FOUND IN TRANSCRIPT and briefly note what was expected."}}
]"""


# ─── BRITISH GAS ────────────────────────────────────────────────────────────

BRITISH_GAS_MANDATORY = """You are a Compliance Auditor for British Gas energy contracts.

CRITICAL ERRORS TO AVOID FOR BRITISH GAS:
1. PRODUCT CONFUSION: Do NOT confuse "Zero Carbon" (renewable + nuclear mix) with "100% Natural Renewable" (renewable only, no nuclear). If the script requires "100% Natural Renewable" and the agent says "mix of renewable and nuclear", that is a FAIL.
2. PRICING PRECISION: For pricing checkpoints, ALL of these must be present: standing charges, unit rates, and contract end dates. If ANY ONE is missing, it is "partial".
3. RENEWAL TERMS: The agent must state the SPECIFIC renewal date and what happens at renewal. Generic "we'll be in touch" is "partial", not "pass".
4. DEEMED RATES: If the script mentions deemed/out-of-contract rates, the agent must have explicitly warned about higher rates after contract ends.
5. KEY PHRASES ARE GUIDES: The agent does not need exact wording — conveying the meaning is sufficient for mandatory checkpoints.

CHECKPOINTS:
{checkpoints_text}

TRANSCRIPT:
{transcript}

For EACH checkpoint return ONLY valid JSON:
[
  {{"name": "checkpoint name", "status": "pass" or "partial" or "fail" or "n_a", "confidence": "high" or "low", "evidence": "exact quote from transcript", "notes": "REQUIRED 1-2 sentence plain-English reasoning for EVERY status. For pass: explain WHY the agent satisfied this checkpoint (what they said and why it covers the requirement). For partial: what was covered vs. what specific piece was missing. For fail: confirm it was NOT FOUND IN TRANSCRIPT and briefly note what was expected."}}
]"""


BRITISH_GAS_CUSTOMER_YES = """You are a Compliance Auditor for British Gas energy contracts.

THESE ARE CUSTOMER CONFIRMATION CHECKPOINTS.

BRITISH GAS SPECIFIC RULES:
1. CREDIT VETTING: The customer must EXPLICITLY consent to credit searches. A general "okay" after a long speech is not sufficient — the agent must have specifically asked about credit checks and the customer must have specifically agreed.
2. AUTHORITY TO ACT: A "clear Yes" is explicitly required by the British Gas script. Ambiguous responses like "mm" or "I suppose" are NOT sufficient — mark as "partial".
3. If the customer's response is ambiguous or cut off, mark as "partial" with confidence "low".

What counts as YES: "yes", "yeah", "yep", "okay", "right", "sure", "that's fine", "correct", "no problem", "go ahead"
What is NOT confirmation: silence, "mm" without clear affirmation, trailing off, agent continuing without waiting

CHECKPOINTS:
{checkpoints_text}

TRANSCRIPT:
{transcript}

For EACH checkpoint return ONLY valid JSON:
[
  {{"name": "checkpoint name", "status": "pass" or "partial" or "fail" or "n_a", "confidence": "high" or "low", "evidence": "quote BOTH agent and customer", "notes": "REQUIRED 1-2 sentence plain-English reasoning for EVERY status. For pass: explain WHY the agent satisfied this checkpoint (what they said and why it covers the requirement). For partial: what was covered vs. what specific piece was missing. For fail: confirm it was NOT FOUND IN TRANSCRIPT and briefly note what was expected."}}
]"""


# ─── SCOTTISH POWER ─────────────────────────────────────────────────────────

SCOTTISH_POWER_MANDATORY = """You are a Compliance Auditor for Scottish Power energy contracts.

SCOTTISH POWER SPECIFIC RULES:
1. QUARTERLY UPDATES: For tariff information checkpoints, the agent MUST mention the specific quarterly update dates (January, April, July, October). Missing the dates = "partial".
2. TPI COMMISSION: The agent must disclose commission in BOTH pounds AND pence per kWh. Missing one = "partial".
3. EARLY TRANSFER: The agent must explain objection rights and the specific process. Generic "you can object" is "partial" — they need to explain HOW.
4. 90-DAY CHECK: Only applies to new owners/tenancies. If the customer is not a new owner, this checkpoint may be N/A — check the context before judging.
5. KEY PHRASES ARE GUIDES: Meaning-for-meaning, not verbatim (except the one verbatim checkpoint).

CHECKPOINTS:
{checkpoints_text}

TRANSCRIPT:
{transcript}

For EACH checkpoint return ONLY valid JSON:
[
  {{"name": "checkpoint name", "status": "pass" or "partial" or "fail" or "n_a", "confidence": "high" or "low", "evidence": "exact quote from transcript", "notes": "REQUIRED 1-2 sentence plain-English reasoning for EVERY status. For pass: explain WHY the agent satisfied this checkpoint (what they said and why it covers the requirement). For partial: what was covered vs. what specific piece was missing. For fail: confirm it was NOT FOUND IN TRANSCRIPT and briefly note what was expected."}}
]"""


SCOTTISH_POWER_CUSTOMER_YES = """You are a Compliance Auditor for Scottish Power energy contracts.

THESE ARE CUSTOMER CONFIRMATION CHECKPOINTS.

SCOTTISH POWER SPECIFIC RULES:
1. TRUNCATED AGREEMENT: If a customer's response trails off (e.g., "yes I will I'll...") and does not reach a complete affirmation, mark as "partial" with confidence "low". An incomplete sentence is NOT confirmation.
2. AUTHORITY CHECKS: Scottish Power requires the customer to confirm they are authorized to select energy suppliers on behalf of their business. A general "yes" to a general question is not enough — the specific authorization question must have been asked.
3. FINAL ACCEPTANCE: The customer must confirm they understood the terms AND are happy to proceed. Two separate confirmations may be needed.

What counts as YES: "yes", "yeah", "yep", "okay", "right", "sure", "that's fine", "correct", "happy to", "go ahead"
What is NOT: trailing off, incomplete sentences, "mm" alone, silence, questions

CHECKPOINTS:
{checkpoints_text}

TRANSCRIPT:
{transcript}

For EACH checkpoint return ONLY valid JSON:
[
  {{"name": "checkpoint name", "status": "pass" or "partial" or "fail" or "n_a", "confidence": "high" or "low", "evidence": "quote BOTH agent and customer", "notes": "REQUIRED 1-2 sentence plain-English reasoning for EVERY status. For pass: explain WHY the agent satisfied this checkpoint (what they said and why it covers the requirement). For partial: what was covered vs. what specific piece was missing. For fail: confirm it was NOT FOUND IN TRANSCRIPT and briefly note what was expected."}}
]"""


# ─── EDF ────────────────────────────────────────────────────────────────────

EDF_MANDATORY = """You are a Compliance Auditor for EDF Energy contracts.

EDF SPECIFIC RULES:
1. METER SAFETY: The phrase "only if it's safe to do so" is a HARD requirement when discussing meter interaction. Missing this = "fail".
2. SMART/AMR: If the site has SMART/AMR, the agent must warn about potential loss of SMART functionality. Generic meter talk without the SMART warning = "partial".
3. BILLING: EDF requires specific mention of billing frequency and payment methods.
4. KEY PHRASES ARE GUIDES for mandatory checkpoints — meaning-for-meaning is acceptable.

CHECKPOINTS:
{checkpoints_text}

TRANSCRIPT:
{transcript}

For EACH checkpoint return ONLY valid JSON:
[
  {{"name": "checkpoint name", "status": "pass" or "partial" or "fail" or "n_a", "confidence": "high" or "low", "evidence": "exact quote", "notes": "REQUIRED 1-2 sentence plain-English reasoning for EVERY status. For pass: explain WHY the agent satisfied this checkpoint (what they said and why it covers the requirement). For partial: what was covered vs. what specific piece was missing. For fail: confirm it was NOT FOUND IN TRANSCRIPT and briefly note what was expected."}}
]"""


EDF_VERBATIM = """You are a Compliance Auditor for EDF Energy contracts.

THESE ARE VERBATIM CHECKPOINTS — the agent must use near-exact wording from the script.

EDF VERBATIM RULES:
1. DIRECT DEBIT SECTIONS: Must be read exactly as defined. Synonyms are NOT allowed.
2. BANK DETAILS: The agent must confirm sort code and account number using the exact format.
3. SIGNATORY: The agent must confirm who is the signatory on the Direct Debit.
4. Acceptable variations: filler words (um, uh), singular/plural, tense, contractions.
5. NOT acceptable: completely different wording, skipping sections, summarizing instead of reading.

CHECKPOINTS:
{checkpoints_text}

TRANSCRIPT:
{transcript}

For EACH checkpoint return ONLY valid JSON:
[
  {{"name": "checkpoint name", "status": "pass" or "partial" or "fail" or "n_a", "confidence": "high" or "low", "evidence": "exact quote", "notes": "REQUIRED 1-2 sentence plain-English reasoning for EVERY status. For pass: explain WHY the agent satisfied this checkpoint (what they said and why it covers the requirement). For partial: what was covered vs. what specific piece was missing. For fail: confirm it was NOT FOUND IN TRANSCRIPT and briefly note what was expected."}}
]"""


EDF_CUSTOMER_YES = """You are a Compliance Auditor for EDF Energy contracts.

THESE ARE CUSTOMER CONFIRMATION CHECKPOINTS.

EDF SPECIFIC:
1. SMART FUNCTIONALITY: For SMART/AMR warnings, the customer must explicitly say they are "happy to submit readings" or equivalent clear agreement.
2. MULTI-METERED: Customer must confirm they understand and are happy with single-meter billing.
3. A clear confirmation is required — not just silence or the agent continuing.

What counts as YES: "yes", "yeah", "yep", "okay", "right", "sure", "that's fine", "happy with that", "happy to"
What is NOT: silence, trailing off, questions, "mm" alone

CHECKPOINTS:
{checkpoints_text}

TRANSCRIPT:
{transcript}

For EACH checkpoint return ONLY valid JSON:
[
  {{"name": "checkpoint name", "status": "pass" or "partial" or "fail" or "n_a", "confidence": "high" or "low", "evidence": "quote BOTH agent and customer", "notes": "REQUIRED 1-2 sentence plain-English reasoning for EVERY status. For pass: explain WHY the agent satisfied this checkpoint (what they said and why it covers the requirement). For partial: what was covered vs. what specific piece was missing. For fail: confirm it was NOT FOUND IN TRANSCRIPT and briefly note what was expected."}}
]"""


# ─── POZITIVE ───────────────────────────────────────────────────────────────

POZITIVE_MANDATORY = """You are a Compliance Auditor for Pozitive Energy contracts.

POZITIVE SPECIFIC RULES:
1. IDENTIFICATION: The agent MUST ask the customer to spell out names, addresses, or positions if there is any ambiguity. If they don't ask to spell, mark as "partial".
2. TIMING: The agent must state the call takes "approximately 8 minutes".
3. GDPR/TERMS: The agent must reference "www.pozitive.energy" and the terms and conditions.
4. CONTRACT INTENT: Must state "legally binding verbal commercial supply contract" — this is a key Pozitive phrase.
5. KEY PHRASES ARE GUIDES for mandatory checkpoints.

CHECKPOINTS:
{checkpoints_text}

TRANSCRIPT:
{transcript}

For EACH checkpoint return ONLY valid JSON:
[
  {{"name": "checkpoint name", "status": "pass" or "partial" or "fail" or "n_a", "confidence": "high" or "low", "evidence": "exact quote", "notes": "REQUIRED 1-2 sentence plain-English reasoning for EVERY status. For pass: explain WHY the agent satisfied this checkpoint (what they said and why it covers the requirement). For partial: what was covered vs. what specific piece was missing. For fail: confirm it was NOT FOUND IN TRANSCRIPT and briefly note what was expected."}}
]"""


POZITIVE_CUSTOMER_YES = """You are a Compliance Auditor for Pozitive Energy contracts.

THESE ARE CUSTOMER CONFIRMATION CHECKPOINTS.

POZITIVE SPECIFIC:
1. BANK DETAILS: For Direct Debit checkpoints, the agent MUST instruct the customer to "pause between each digit". If they don't, mark as "partial" even if the numbers are correct.
2. TERMS AGREEMENT: The customer must explicitly agree to terms and conditions AND privacy policy. Two separate acknowledgments may be needed.
3. Most Pozitive checkpoints are customer_yes (27 out of 37) — be thorough on confirmation detection.

What counts as YES: "yes", "yeah", "yep", "okay", "right", "sure", "that's fine", "correct", "I agree"
What is NOT: silence, trailing off, agent continuing without pause, "mm" alone

CHECKPOINTS:
{checkpoints_text}

TRANSCRIPT:
{transcript}

For EACH checkpoint return ONLY valid JSON:
[
  {{"name": "checkpoint name", "status": "pass" or "partial" or "fail" or "n_a", "confidence": "high" or "low", "evidence": "quote BOTH agent and customer", "notes": "REQUIRED 1-2 sentence plain-English reasoning for EVERY status. For pass: explain WHY the agent satisfied this checkpoint (what they said and why it covers the requirement). For partial: what was covered vs. what specific piece was missing. For fail: confirm it was NOT FOUND IN TRANSCRIPT and briefly note what was expected."}}
]"""


# ─── GENERIC FALLBACK ───────────────────────────────────────────────────────

GENERIC_MANDATORY = """You are a compliance auditor. Check each checkpoint below.

RULES:
1. The agent must have CONVEYED THE INFORMATION — exact wording is NOT required
2. The key phrases are GUIDES, not exact requirements
3. If the agent said the same thing in different words → PASS
4. Only "fail" if genuinely NOT communicated
5. Only "partial" if KEY information was missing
6. Be fair and balanced — do not lean toward pass or fail

CHECKPOINTS:
{checkpoints_text}

TRANSCRIPT:
{transcript}

For EACH checkpoint return ONLY valid JSON:
[
  {{"name": "checkpoint name", "status": "pass" or "partial" or "fail" or "n_a", "confidence": "high" or "low", "evidence": "exact quote", "notes": "REQUIRED 1-2 sentence plain-English reasoning for EVERY status. For pass: explain WHY the agent satisfied this checkpoint (what they said and why it covers the requirement). For partial: what was covered vs. what specific piece was missing. For fail: confirm it was NOT FOUND IN TRANSCRIPT and briefly note what was expected."}}
]"""


GENERIC_CUSTOMER_YES = """You are a compliance auditor. Check each checkpoint below.

TWO things required:
1. AGENT stated the information
2. CUSTOMER gave a CLEAR affirmative response

YES: "yes", "yeah", "yep", "mmhmm", "okay", "right", "sure", "that's fine", "correct", "go ahead"
NOT: silence, agent continuing without pause, questions, "what?", "sorry?"

Agent said it + customer confirmed → "pass"
Agent said it + no confirmation → "partial"
Agent didn't say it → "fail"

CHECKPOINTS:
{checkpoints_text}

TRANSCRIPT:
{transcript}

For EACH checkpoint return ONLY valid JSON:
[
  {{"name": "checkpoint name", "status": "pass" or "partial" or "fail" or "n_a", "confidence": "high" or "low", "evidence": "quote BOTH agent and customer", "notes": "REQUIRED 1-2 sentence plain-English reasoning for EVERY status. For pass: explain WHY the agent satisfied this checkpoint (what they said and why it covers the requirement). For partial: what was covered vs. what specific piece was missing. For fail: confirm it was NOT FOUND IN TRANSCRIPT and briefly note what was expected."}}
]"""


GENERIC_VERBATIM = """You are a compliance auditor. Check each checkpoint below.

VERBATIM: Agent must use near-exact wording from the script.
Acceptable: filler words, singular/plural, tense, contractions.
NOT acceptable: completely different wording, skipping sections.

CHECKPOINTS:
{checkpoints_text}

TRANSCRIPT:
{transcript}

For EACH checkpoint return ONLY valid JSON:
[
  {{"name": "checkpoint name", "status": "pass" or "partial" or "fail" or "n_a", "confidence": "high" or "low", "evidence": "exact quote", "notes": "REQUIRED 1-2 sentence plain-English reasoning for EVERY status. For pass: explain WHY the agent satisfied this checkpoint (what they said and why it covers the requirement). For partial: what was covered vs. what specific piece was missing. For fail: confirm it was NOT FOUND IN TRANSCRIPT and briefly note what was expected."}}
]"""


# ─── ROUTING ────────────────────────────────────────────────────────────────

# Supplier → {strictness → prompt}
SUPPLIER_PROMPTS = {
    "E.ON Next": {
        "mandatory": EON_NEXT_MANDATORY,
        "customer_yes": EON_NEXT_CUSTOMER_YES,
        "verbatim": GENERIC_VERBATIM,  # E.ON has no verbatim checkpoints
    },
    "British Gas": {
        "mandatory": BRITISH_GAS_MANDATORY,
        "customer_yes": BRITISH_GAS_CUSTOMER_YES,
        "verbatim": GENERIC_VERBATIM,  # BG has no verbatim checkpoints
    },
    "Scottish Power": {
        "mandatory": SCOTTISH_POWER_MANDATORY,
        "customer_yes": SCOTTISH_POWER_CUSTOMER_YES,
        "verbatim": GENERIC_VERBATIM,  # SP has 1 verbatim — use generic
    },
    "EDF": {
        "mandatory": EDF_MANDATORY,
        "customer_yes": EDF_CUSTOMER_YES,
        "verbatim": EDF_VERBATIM,
    },
    "Pozitive": {
        "mandatory": POZITIVE_MANDATORY,
        "customer_yes": POZITIVE_CUSTOMER_YES,
        "verbatim": GENERIC_VERBATIM,  # Pozitive has no verbatim
    },
}


# Evidence is the product's core value — every verdict must be backed by an
# exact transcript quote that a reviewer can locate in the call. Empty
# evidence on a PASS/PARTIAL used to pass the verification step as "100%
# similarity" (because empty matches empty trivially), which made the UI
# advertise fake confidence. This preamble is prepended to every supplier
# prompt so the JSON contract is unambiguous regardless of which variant
# the router picked.
_EVIDENCE_RULE = """
EVIDENCE CONTRACT — READ THIS FIRST, IT OVERRIDES LATER INSTRUCTIONS WHERE THEY CONFLICT:

For every checkpoint you return:
1. If status is "pass" or "partial", the "evidence" field MUST contain a
   VERBATIM quote (8–40 words) copied directly from the TRANSCRIPT below.
   Do NOT paraphrase. Do NOT invent. Copy the exact words the speaker said.
2. If you cannot find a direct quote that supports pass/partial, the
   status MUST be "fail" with "evidence": "NOT FOUND IN TRANSCRIPT".
3. Never return an empty evidence string on pass/partial. Never return
   evidence text that does not appear in the transcript word-for-word.
4. If the rule requires both the agent and the customer's response, quote
   both consecutively in the same evidence field.

CONDITIONAL CHECKPOINTS — N/A RULE (2026-05-27, closes the "if applicable"
phantom-failure pattern):

If the checkpoint NAME contains the phrase "if applicable", "if relevant",
"if asked", "if requested", "if the customer", "if the call",
"where applicable", "where required", "when applicable", "when required",
"subject to", "only if", "on request", "if any", or any other
conditional qualifier, AND the condition does NOT apply to this call's
content, return:

    "status": "n_a",
    "evidence": "CONDITIONAL NOT TRIGGERED",
    "notes": "<1 sentence explaining which condition needs to fire and why
              it does not in this transcript>"

Examples of n_a vs fail:
- "State 100% renewable benefit if applicable" — when the contract being
  agreed is NOT a renewable tariff, return n_a (the conditional doesn't
  fire). Return fail only if it IS a renewable tariff and the agent
  failed to state the benefit.
- "Disclose ASC and excess capacity charges if applicable" — when there
  is no ASC/excess capacity component in this contract, return n_a.
  Return fail only when the contract HAS these charges and the agent
  did not disclose them.
- "Authorise contacting current supplier on objections" — when the
  customer raises no objections and the LOA does not require this
  authorisation, return n_a.

N/A is NOT the same as fail. N/A checkpoints are excluded from the score
denominator — they neither help nor hurt the compliance rate. Always
prefer fail over n_a when there is genuine evidence of a missed
obligation. Use n_a only when the conditional precondition is absent.

Violating this contract invalidates the entire response for that checkpoint.

"""


def get_prompt(supplier: str, strictness: str) -> str:
    """Get the best prompt for a supplier + strictness combination."""
    # Try exact supplier match
    supplier_prompts = SUPPLIER_PROMPTS.get(supplier)
    base = None
    if supplier_prompts:
        base = supplier_prompts.get(strictness)

    # Try partial supplier match (e.g., "E.ON Next" in "EON_Next__Gas_Verbal")
    if base is None:
        for key, prompts in SUPPLIER_PROMPTS.items():
            if key.lower() in supplier.lower() or supplier.lower() in key.lower():
                base = prompts.get(strictness)
                if base:
                    break

    # Fallback to generic
    if base is None:
        if strictness == "customer_yes":
            base = GENERIC_CUSTOMER_YES
        elif strictness == "verbatim":
            base = GENERIC_VERBATIM
        else:
            base = GENERIC_MANDATORY

    return _EVIDENCE_RULE + base


# ─── PROMPT VERSIONING (Phase J Task 32) ────────────────────────────────────
#
# Every VerdictHistory row carries a `prompt_version` — a 12-char sha256 of the
# exact prompt text applied to that checkpoint. Hashes are memoized per
# supplier on first call so tests and the hot path pay the cost once per
# process. Changing any supplier's prompt (this module) or playbook (the
# markdown files in backend/skills/) changes the hash, which lets ops query
# override-rate-by-version to spot regressions from prompt edits.

def _hash(text: str) -> str:
    """First 12 chars of sha256 — short enough to grep logs, wide enough to
    avoid collisions across the handful of prompt revisions we'll ever have."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _compose_prompt_source(supplier: str | None) -> str:
    """Build the canonical text whose hash becomes the prompt version for a
    supplier.

    We combine two sources:
      1. The supplier's playbook + generic playbook (what the agent actually
         sends as the system prompt — see agent_loop._build_system_prompt).
      2. A deterministic serialization of this module's SUPPLIER_PROMPTS
         entry (the strictness-routed templates consumed by
         checkpoint_analyzer.get_prompt). Included so an edit to EON_NEXT_*
         constants shows up as a new version even for code paths that don't
         touch the playbook.

    Missing-supplier path: falls back to the generic playbook + the generic
    prompt constants. That same fallback is what both downstream callers use,
    so the hash stays consistent with reality.
    """
    # Late import — playbooks reads the filesystem, and keeping it lazy
    # avoids circular-import risk if prompts.py is ever imported during
    # Alembic env setup.
    from app.agent.playbooks import load_combined_playbook

    playbook_text = load_combined_playbook(supplier or "")

    # Deterministic serialization of the strictness-routed prompt table.
    # Sort keys so dict ordering never perturbs the hash across Python
    # versions / interpreters.
    supplier_key = supplier if supplier and supplier in SUPPLIER_PROMPTS else None
    if supplier_key:
        prompts_dict = SUPPLIER_PROMPTS[supplier_key]
        static_part = "\n\n".join(
            f"[{k}]\n{prompts_dict[k]}" for k in sorted(prompts_dict)
        )
    else:
        static_part = (
            f"[mandatory]\n{GENERIC_MANDATORY}\n\n"
            f"[customer_yes]\n{GENERIC_CUSTOMER_YES}\n\n"
            f"[verbatim]\n{GENERIC_VERBATIM}"
        )

    return f"PLAYBOOK\n{playbook_text}\n\n---\n\nSTATIC\n{static_part}"


# Cache: supplier (canonical name, or "_default" for unknown) → 12-char hash.
# Populated lazily by version_for_supplier(). Cleared only in tests via
# _reset_version_cache().
_VERSION_CACHE: dict[str, str] = {}


def version_for_supplier(supplier: str | None) -> str:
    """Return the 12-char prompt version for this supplier (memoized).

    Unknown / missing suppliers resolve to "_default" — the generic-playbook
    + generic-prompt hash. Stable across calls; the first call for a given
    supplier pays the filesystem + hash cost, subsequent calls are O(1).
    """
    key = supplier or "_default"
    cached = _VERSION_CACHE.get(key)
    if cached is not None:
        return cached
    version = _hash(_compose_prompt_source(supplier))
    _VERSION_CACHE[key] = version
    return version


def _reset_version_cache() -> None:
    """Test helper: drop the memoized hashes so a mid-test mutation of a
    prompt constant re-computes on the next call. Not called by production
    code."""
    _VERSION_CACHE.clear()


def format_checkpoints_for_prompt(checkpoints: list[dict]) -> str:
    """Format checkpoint definitions for insertion into prompts."""
    text = ""
    for cp in checkpoints:
        text += f"\nCHECKPOINT: {cp['name']}\n"
        text += f"  Required: {cp.get('required', '')}\n"
        text += f"  Key phrases (guides): {', '.join(cp.get('key_phrases', []))}\n"
        if cp.get("customer_response_required"):
            text += f"  ⚠️ Customer must explicitly confirm\n"
    return text


# Keep backward compatibility
def get_prompt_for_strictness(strictness: str) -> str:
    """Legacy: get generic prompt by strictness only."""
    if strictness == "customer_yes":
        return GENERIC_CUSTOMER_YES
    elif strictness == "verbatim":
        return GENERIC_VERBATIM
    return GENERIC_MANDATORY
