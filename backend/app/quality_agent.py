"""Quality AI Agent — resolves identity ambiguities across sibling calls.

Heuristics + per-call LLM extraction get us 80% of the way. The remaining
20% (which call is which customer, what's the canonical business name,
who's the actual agent vs the actual customer) needs cross-call reasoning
that no single-call prompt can do. This module wraps Opus 4.7 with a
strong system prompt so the model takes the role of a senior compliance
analyst reviewing a stack of related calls and producing one canonical
identity record.

Used by:
- pipeline.py post-finalize: when a call has 1+ sibling candidates by
  business / human name, run the agent and apply the verdict (rename
  customer, merge deals, correct agent attribution).
- /api/admin/quality-resolve endpoint: backfills existing calls.

Output schema is strictly typed — anything the agent returns outside
the schema is logged + ignored, so a hallucinating model can't corrupt
state.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from app.analysis import _call_llm
from app.logger import log
from app.resilience import LLM_RETRY


# Single-prompt design — kept as a system block so Anthropic prompt
# caching kicks in across batched runs (every Quality Agent call shares
# the same prefix; only the per-call data changes).
QUALITY_AGENT_SYSTEM = """You are the Quality AI Agent for Watt Utilities' compliance auditing system.

Watt is a UK third-party-intermediary energy broker. Each call below is
a recorded conversation between a Watt sales agent (the BROKER) and a
business customer about an energy supply contract with one of these
suppliers: BGL (British Gas Lite), British Gas, EDF, E.ON Next,
Pozitive Energy, Scottish Power.

Your job: read the candidate calls and return ONE canonical identity
record. Cross-reference every call. The same customer may appear under
slightly different names across calls — pick the most accurate, most
specific, most-frequently-mentioned canonical form.

DECIDE:

1. canonical_customer_name (string) — the legal / trading name of the
   business. Prefer the form that includes specifics ("St. Peter's
   Benfleet Church" beats "The Church" beats "Evangelical Church").
   If only a person's name appears, return their full name.

2. customer_person (string or null) — the human decision-maker on the
   line (the one who said "yes speaking", the business owner). NEVER
   the broker. Pick ONE name across all calls. Resolve variants
   ("Christopher", "Christopher Neil Bank", "Christopher Neil Banks") to
   the most complete form.

3. agent_name (string or null) — the Watt broker's first name (or full
   name if given). They self-introduce ("my name is X", "calling from
   Watt", "calling about your energy supply"). NEVER the customer's name.
   If multiple agents handled different calls, return the most-recent.

4. supplier (one of: "BGL", "British Gas", "EDF", "E.ON Next",
   "Pozitive Energy", "Scottish Power", or "Unknown") — the energy
   supplier whose contract is being sold across these calls. Cross-
   validate: if one call says E.ON Next and another doesn't mention a
   supplier, infer E.ON Next for the whole set.

5. call_classifications (object: call_id → call_type) — for each call,
   classify call_type as:
     - "lead_gen" : opening qualification call, decision-maker check,
                    rate-comparison teaser
     - "closer"   : verbal-contract / acquisition call where the customer
                    confirms acceptance of the rates and authorises
     - "loa"      : standalone Letter of Authority call (only on suppliers
                    that require a separate LOA, never E.ON Next)
     - "amendment": amendment / re-quote call after a verbal contract
     - "c_call"   : courtesy callback after submission

6. stitch (one of: "merge_all", "keep_separate", "partial_merge") —
   should these calls belong to the SAME customer + same deal?
     - merge_all     : every call is the same customer + same deal
     - keep_separate : each call is a genuinely different customer/deal
     - partial_merge : some calls match, others don't (rare; use only
                       when the evidence is clear)

7. stitch_reason (string, ≤140 chars) — short justification.

8. confidence (number, 0.0–1.0) — how confident you are. Use ≥0.8 only
   when multiple calls clearly reference the same business or person.

RULES:
- Two names match if the human's full name appears (or substring) in
  multiple calls. "Christopher Neil Bank" and "Christopher Neil Banks"
  are the same person — case-insensitive substring + token-overlap.
- Business name extraction is unreliable; trust customer_person more.
- Never invent suppliers, names, or call types. Use "Unknown" / null.
- Output STRICT JSON. No markdown, no prose outside JSON.
"""

QUALITY_AGENT_USER_TEMPLATE = """## Candidate calls

{calls_block}

Return the canonical identity record as STRICT JSON matching the schema
in the system prompt. JSON ONLY."""


_SUPPLIER_VOCAB = {
    "bgl",
    "british gas",
    "edf",
    "e.on next",
    "pozitive energy",
    "scottish power",
    "unknown",
}

_CALL_TYPE_VOCAB = {"lead_gen", "closer", "loa", "amendment", "c_call"}
_STITCH_VOCAB = {"merge_all", "keep_separate", "partial_merge"}


def _format_call_for_agent(call: dict) -> str:
    """Render one call as a compact block the agent can read."""
    cid = str(call.get("id", ""))
    fn = call.get("filename", "")
    sup = call.get("detected_supplier", "")
    agent = call.get("agent_name", "")
    cust = call.get("customer_name", "")
    score = call.get("score", "")
    transcript = (call.get("transcript") or "")[:4000]
    return (
        f"### Call {cid[:8]}\n"
        f"  filename       : {fn}\n"
        f"  detected_supplier : {sup}\n"
        f"  agent_name     : {agent}\n"
        f"  customer_name  : {cust}\n"
        f"  score          : {score}\n"
        f"  transcript     :\n{transcript}\n"
    )


def _parse_agent_response(raw: str) -> Optional[dict]:
    """Extract + validate the JSON response. Returns None on bad shape."""
    if not raw:
        return None
    txt = raw.strip()
    if txt.startswith("```"):
        txt = txt.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    # Some models add prose before/after — extract the first {...} block.
    match = re.search(r"\{.*\}", txt, re.DOTALL)
    if not match:
        log.warning("QualityAgent: no JSON object in response")
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError as e:
        log.warning(f"QualityAgent: JSON parse failed: {e}")
        return None

    # Coerce + validate.
    out: dict[str, Any] = {}
    out["canonical_customer_name"] = str(parsed.get("canonical_customer_name") or "").strip() or None
    out["customer_person"] = str(parsed.get("customer_person") or "").strip() or None
    out["agent_name"] = str(parsed.get("agent_name") or "").strip() or None

    sup = str(parsed.get("supplier") or "").strip()
    if sup.lower() not in _SUPPLIER_VOCAB:
        sup = "Unknown"
    out["supplier"] = sup

    cls_raw = parsed.get("call_classifications") or {}
    classifications: dict[str, str] = {}
    if isinstance(cls_raw, dict):
        for k, v in cls_raw.items():
            v_norm = str(v).strip().lower()
            if v_norm in _CALL_TYPE_VOCAB:
                classifications[str(k)] = v_norm
    out["call_classifications"] = classifications

    stitch = str(parsed.get("stitch") or "").strip().lower()
    if stitch not in _STITCH_VOCAB:
        stitch = "keep_separate"
    out["stitch"] = stitch
    out["stitch_reason"] = str(parsed.get("stitch_reason") or "").strip()[:140]

    conf = parsed.get("confidence")
    try:
        cf = float(conf) if conf is not None else 0.0
    except (TypeError, ValueError):
        cf = 0.0
    out["confidence"] = max(0.0, min(1.0, cf))

    return out


@LLM_RETRY
async def resolve_identity(calls: list[dict]) -> Optional[dict]:
    """Send a batch of candidate calls to the Quality Agent and return
    its canonical identity verdict. ``calls`` is a list of dicts with
    keys: id, filename, detected_supplier, agent_name, customer_name,
    score, transcript.
    """
    if not calls:
        return None
    calls_block = "\n\n".join(_format_call_for_agent(c) for c in calls)
    user = QUALITY_AGENT_USER_TEMPLATE.format(calls_block=calls_block)
    log.info(f"🤖 QualityAgent calling Opus on {len(calls)} call(s)")
    try:
        raw = await _call_llm(user, timeout=60.0, system=QUALITY_AGENT_SYSTEM)
    except Exception as e:
        log.warning(f"QualityAgent LLM call failed: {e}")
        return None
    parsed = _parse_agent_response(raw)
    if parsed:
        log.info(
            f"🤖 QualityAgent → customer=\"{parsed.get('canonical_customer_name')}\" "
            f"person=\"{parsed.get('customer_person')}\" "
            f"agent=\"{parsed.get('agent_name')}\" "
            f"supplier=\"{parsed.get('supplier')}\" "
            f"stitch={parsed.get('stitch')} confidence={parsed.get('confidence'):.2f}"
        )
    return parsed
