"""Auto-feedback email after compliance analysis.

Triggered by the `call/finalized` Inngest event. The email body uses
the rejection's ``fix_required`` text directly (which the LLM is
prompted to write in the ops-team house style — see
``app/watt_compliance/prompts.py``). This makes the agent's inbox
match the wording of the existing manual rejection list XLSX.

Provider abstraction is intentionally minimal — we plumb to a generic
HTTPS endpoint (Resend / Postmark / SendGrid all have HTTPS POST APIs
that accept a JSON body of the same shape). The vendor is selected by
env var so we don't hard-couple to one SaaS.

This module is a SCAFFOLD — it does not actually send when the env
isn't configured. The notification is skipped silently with a debug
log so the pipeline keeps moving in dev / pre-credential states.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

import httpx

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FeedbackEmailPayload:
    to: str                      # recipient (sales agent or compliance lead)
    customer_name: str
    call_id: str
    rejections: list[dict]       # rejection dicts: reason_code / category / severity / fix_required
    overall_verdict: str         # PASS | REVIEW | COACH | BLOCK
    score: int


def _render_subject(p: FeedbackEmailPayload) -> str:
    if p.overall_verdict == "BLOCK":
        return f"[BLOCK] {p.customer_name} — compliance issues need amendment"
    if p.overall_verdict == "REVIEW":
        return f"[REVIEW] {p.customer_name} — manual review required"
    if p.overall_verdict == "COACH":
        return f"[COACH] {p.customer_name} — minor coaching points"
    return f"[PASS] {p.customer_name} — compliance check clean"


def _render_body(p: FeedbackEmailPayload) -> str:
    """Plain-text body in the ops-team's existing house style."""
    lines: list[str] = []
    lines.append(f"Hi,")
    lines.append("")
    if p.overall_verdict == "BLOCK":
        lines.append(f"Above call ({p.customer_name}) has not passed compliance "
                     f"for the following reason(s):")
    elif p.overall_verdict == "PASS":
        lines.append(f"The call for {p.customer_name} passed compliance. "
                     f"No action required (score {p.score}/100).")
        return "\n".join(lines)
    else:
        lines.append(f"Compliance review on the call for {p.customer_name} "
                     f"(score {p.score}/100):")
    lines.append("")
    for r in p.rejections:
        sev = r.get("severity", "")
        code = r.get("reason_code", "")
        cat = r.get("category", "")
        fix = r.get("fix_required") or r.get("evidence_quote") or "(no fix proposed)"
        lines.append(f"- [{sev}] {code} ({cat}): {fix}")
    lines.append("")
    lines.append("Once corrected, please put this back through and the system "
                 "will reverify on the amendment call.")
    lines.append("")
    lines.append("— Watt Compliance system")
    return "\n".join(lines)


async def send_feedback_email(
    payload: FeedbackEmailPayload,
    *,
    api_endpoint: str | None = None,
    api_key: str | None = None,
    from_address: str | None = None,
    timeout: float = 10.0,
) -> bool:
    """Send the feedback email via the configured HTTPS provider.

    Returns True on success, False on any failure (logged, not raised).
    Skipped silently when ``api_endpoint`` or ``api_key`` is unset — the
    pipeline must keep running in pre-credential states.
    """
    if not api_endpoint or not api_key:
        log.debug("feedback_email_skipped reason=unconfigured to=%s", payload.to)
        return False

    body = {
        "from": from_address or "compliance@watt.local",
        "to": [payload.to],
        "subject": _render_subject(payload),
        "text": _render_body(payload),
        "tags": [
            {"name": "verdict", "value": payload.overall_verdict.lower()},
            {"name": "call_id", "value": payload.call_id},
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                api_endpoint,
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                json=body,
            )
        if r.status_code >= 300:
            log.warning("feedback_email_http_%s body=%s", r.status_code, r.text[:200])
            return False
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("feedback_email_failed: %s", e)
        return False


def build_feedback_payload_from_analysis(
    *,
    to: str,
    customer_name: str,
    call_id: str,
    analysis_result: dict,
) -> FeedbackEmailPayload:
    """Convenience — build the payload from the dict produced by
    ``app/analysis.py:analyze_compliance_watt``."""
    rejections: Iterable[dict] = analysis_result.get("rejections") or []
    return FeedbackEmailPayload(
        to=to,
        customer_name=customer_name,
        call_id=call_id,
        rejections=list(rejections),
        overall_verdict=str(analysis_result.get("verdict", "REVIEW")).upper(),
        score=int(analysis_result.get("score", 0)),
    )
