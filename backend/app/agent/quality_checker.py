"""Quality Checker Agent — post-pipeline audit pass over each finalised call.

Owner mandate (2026-05-27): every record needs a second-opinion AI agent
that re-reads the transcript + the verdicts and flags inconsistencies
before the call goes to a human reviewer. Inputs include:

* the FULL transcript
* the chosen agent_name, customer_name, detected_supplier, call_type
* the per-checkpoint verdict list (pass / fail / partial / n_a / unverified)
* the segment buckets + compliance_status

Output is a structured ``quality_check`` JSON object persisted onto the
``calls.quality_check`` JSON column (added by migration
``2026_05_27_quality_check``). Shape:

    {
      "verdict": "ok" | "review" | "block",
      "issues": [
        {
          "code": "AGENT_NAME_TRANSFER_TARGET" | ... ,
          "severity": "high" | "medium" | "low",
          "field": "agent_name",
          "expected": "Jack Giles",
          "got": "Bradley",
          "evidence": "[00:47] Agent: i'm gonna get you through to bradley now",
          "fix_required": "rerun_detect_names | manual_override"
        }
      ],
      "score": 0.0–1.0,                    // overall quality score
      "model": "anthropic/claude-opus-4.7",
      "checked_at": "<ISO8601>"
    }

The checker is wired into ``pipeline.process_call`` immediately after
``L3_LIFECYCLE`` so reviewers see the second-opinion verdict alongside
the primary AI verdict on the call detail page. Failure is non-fatal —
a checker exception is logged + the call still finalises so the human
reviewer can always intervene.

Routes through the project's standard ``_call_llm`` so it inherits the
OpenRouter wiring, prompt-caching, retry, and semaphore back-pressure.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from app.analysis import _call_llm
from app.logger import log


_PROMPT = """You are a Quality Checker AI agent for an energy-brokerage
compliance review system. Your job is to AUDIT another AI agent's
verdict on a call recording — NOT to re-grade the call from scratch.

You receive:
* The full call transcript (diarised, with Agent: / Customer: tags and
  [mm:ss] timestamps — the diarisation can be wrong, so judge by content
  not labels).
* The primary AI agent's extractions: agent_name, customer_name,
  detected_supplier, call_type.
* A summary of per-checkpoint verdicts.

You return a JSON object listing INCONSISTENCIES between what the
transcript actually shows and what the primary AI agent recorded.

CHECKS YOU PERFORM (in order):

1. AGENT NAME — is the recorded agent_name the actual SPEAKER who
   carries this call? Specifically watch for:
   - Lead-gen TRANSFER targets: if the agent_name appears only in a
     hand-off line like "i'm gonna get you through to <name>",
     "i'll transfer you to <name>", "<name> is my pricing manager"
     — then the recorded agent_name is WRONG. The real agent is the
     SELF-INTRODUCING speaker earlier in the transcript.
     Example: transcript opens "yeah that's me jack giles" and ends
     "i'm gonna get you through to bradley now" — agent_name MUST be
     "Jack Giles" (the opener), NOT "Bradley" (the transfer target).
   - Mis-attributed first names: the agent_name is a customer's name
     or a third party's name accidentally captured.

2. CUSTOMER NAME — is the customer_name the person who OWNS or RUNS
   the business? Flag if:
   - It looks like an agent name leaked into the customer slot.
   - It's a company name instead of a person.
   - It's a PII redaction token like "[PERSON_NAME]".

3. SUPPLIER — does the transcript mention the recorded supplier in a
   context that matches the call's intent (renewal, sign-up,
   amendment)? Flag if the supplier is "Unknown" yet the transcript
   clearly names one (e.g. "your current supplier is E.ON Next"
   appears but supplier="Unknown").

4. CALL TYPE — does the recorded call_type match the transcript?
   - lead_gen: opener qualifies the customer + hands off
   - verbal: a closer takes the customer through the verbal contract
     (script disclosures, payment, T&Cs)
   - loa: just the Letter of Authority — short, terms only
   - pre_sales: cold qualification, no contract
   Mismatch is flagged but not blocking.

5. VERDICT CONSISTENCY (sample 3-5 checkpoints with confidence=low) —
   does the verdict (pass / fail / partial / n_a) align with what
   the transcript actually says? If you see clear contradictions
   (e.g. the agent SAID the required phrase but verdict=fail), flag.
   Do NOT re-grade every checkpoint — sample only.

OUTPUT FORMAT — exactly this JSON, no prose:

{{
  "verdict": "ok" | "review" | "block",
  "issues": [
    {{
      "code": "<UPPERCASE_SNAKE_CODE>",
      "severity": "high" | "medium" | "low",
      "field": "agent_name" | "customer_name" | "supplier" | "call_type" | "checkpoint_verdict" | "other",
      "expected": "<correct value as YOU see it from the transcript>",
      "got": "<value the primary AI recorded>",
      "evidence": "<exact 5-20 word transcript quote that proves the discrepancy, or empty if no single quote>",
      "fix_required": "rerun_detect_names" | "rerun_business_detect" | "rerun_supplier_detect" | "rerun_call_type" | "manual_review" | "no_action"
    }}
  ],
  "score": <0.0 to 1.0 — 1.0 = no issues, 0.0 = primary AI got everything wrong>,
  "summary": "<one-sentence explanation of the overall result for the reviewer>"
}}

CALIBRATION:
- "ok" verdict + empty issues + score≥0.95 — primary AI nailed it.
- "review" verdict + 1-3 medium issues + score 0.5-0.94 — minor drift,
  human can fix in seconds.
- "block" verdict + ≥1 high issue + score<0.5 — primary AI made a
  load-bearing error (wrong agent, wrong supplier on a 4-stage deal,
  call_type misclassified). Reviewer must intervene before the call
  hits the tracker.

Never invent issues. If there's no discrepancy, return
"verdict": "ok", "issues": [], "score": 1.0, "summary": "...".

INPUT — PRIMARY AI EXTRACTIONS:
  agent_name:        {agent_name}
  customer_name:     {customer_name}
  detected_supplier: {supplier}
  call_type:         {call_type}
  compliance_status: {compliance_status}
  bucket:            {bucket}

INPUT — CHECKPOINT VERDICT SUMMARY:
{verdict_summary}

INPUT — TRANSCRIPT (truncated to first 8000 chars if longer):
{transcript}
"""


def _summarise_verdicts(checkpoint_results: list[dict] | None) -> str:
    """Compact summary of per-checkpoint verdicts for the prompt.

    Format: ``NAME -> status (conf)`` one per line. Capped at 25 rows
    to keep token use sane; the checker samples — it doesn't re-grade.
    """
    if not checkpoint_results:
        return "(no checkpoints recorded)"
    lines: list[str] = []
    for cp in checkpoint_results[:25]:
        if not isinstance(cp, dict):
            continue
        name = (cp.get("name") or "?")[:60]
        status = cp.get("status") or "?"
        conf = cp.get("confidence") or "?"
        lines.append(f"  - {name}: {status} ({conf})")
    if len(checkpoint_results) > 25:
        lines.append(f"  ... +{len(checkpoint_results) - 25} more not shown")
    return "\n".join(lines) or "(no parseable checkpoint rows)"


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
    return text.strip()


async def quality_check(
    *,
    transcript: str,
    agent_name: str | None,
    customer_name: str | None,
    detected_supplier: str | None,
    call_type: str | None,
    compliance_status: str | None,
    bucket: str | None,
    checkpoint_results: list[dict] | None,
    timeout: float = 45.0,
) -> dict[str, Any]:
    """Run the Quality Checker AI agent. Returns the parsed JSON dict.

    Always returns a valid envelope, even on LLM error: a swallowed
    error becomes a `verdict="review"` row with an `error` issue so
    the human reviewer notices the auditor itself failed and can
    decide whether to retry.

    Routes through ``_call_llm`` so it inherits the OpenRouter wiring,
    semaphores, and retry. ``cheap=False`` — quality checking is a
    judgment task that needs Opus 4.7.
    """
    started = time.time()
    prompt = _PROMPT.format(
        agent_name=agent_name or "Unknown",
        customer_name=customer_name or "Unknown",
        supplier=detected_supplier or "Unknown",
        call_type=call_type or "Unknown",
        compliance_status=compliance_status or "pending",
        bucket=bucket or "n/a",
        verdict_summary=_summarise_verdicts(checkpoint_results),
        transcript=(transcript or "")[:8000],
    )
    log.info("🕵️ QUALITY_CHECK start — calling Opus 4.7")
    try:
        raw = await _call_llm(prompt, timeout=timeout)
    except Exception as e:
        log.warning(f"🕵️ QUALITY_CHECK LLM call failed: {e!r}")
        return {
            "verdict": "review",
            "issues": [
                {
                    "code": "QC_LLM_FAILED",
                    "severity": "low",
                    "field": "other",
                    "expected": "",
                    "got": "",
                    "evidence": "",
                    "fix_required": "no_action",
                }
            ],
            "score": 0.5,
            "summary": f"Quality checker errored: {type(e).__name__}; manual review only.",
            "model": "anthropic/claude-opus-4.7",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_ms": int((time.time() - started) * 1000),
        }

    text = _strip_code_fences(raw)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        log.warning(f"🕵️ QUALITY_CHECK non-JSON output: {e}; raw[:200]={text[:200]!r}")
        return {
            "verdict": "review",
            "issues": [
                {
                    "code": "QC_OUTPUT_PARSE_FAILED",
                    "severity": "low",
                    "field": "other",
                    "expected": "valid JSON",
                    "got": text[:200],
                    "evidence": "",
                    "fix_required": "no_action",
                }
            ],
            "score": 0.5,
            "summary": "Quality checker returned malformed JSON.",
            "model": "anthropic/claude-opus-4.7",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_ms": int((time.time() - started) * 1000),
        }

    # Defensive defaults: the LLM occasionally drops fields.
    verdict = parsed.get("verdict") or "review"
    if verdict not in ("ok", "review", "block"):
        verdict = "review"
    issues = parsed.get("issues") or []
    if not isinstance(issues, list):
        issues = []
    try:
        score = float(parsed.get("score", 0.5))
    except (TypeError, ValueError):
        score = 0.5
    score = max(0.0, min(1.0, score))
    summary = parsed.get("summary") or ""

    envelope = {
        "verdict": verdict,
        "issues": issues,
        "score": score,
        "summary": summary,
        "model": "anthropic/claude-opus-4.7",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_ms": int((time.time() - started) * 1000),
    }
    log.info(
        f"🕵️ QUALITY_CHECK done verdict={verdict} score={score:.2f} "
        f"issues={len(issues)} elapsed_ms={envelope['elapsed_ms']}"
    )
    return envelope
