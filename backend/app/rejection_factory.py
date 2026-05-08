"""Pure functions that turn a finalized Call into an unsaved Rejection.

Side-effect-free so callers (pipeline finalize step) own the persistence
boundary. LLM calls are mockable for unit tests.

Vocabulary note: the W4 category enum below mirrors the CHECK constraint
defined in alembic ``b1d4f7e2c903_w2_rejections.py`` and the canonical set
in ``rejections_routes.REJECTION_CATEGORIES``. Keep these three lists in
sync — divergence will produce IntegrityErrors at insert time.

Each LLM helper accepts an optional ``record_trace`` callback that the
caller (``build_rejection_for_call``) wires up to buffer per-turn reasoning
into ``agent_traces`` — same pattern as ``checkpoint_analyzer`` so the
HITL UI's "AI reasoning" expander can show why a category/fix/narrative
was picked.
"""
from __future__ import annotations

import time
import uuid
from typing import Callable, Optional

from app.analysis import _call_llm
from app.logger import log

# (raw_llm_output, latency_ms, prompt) → None. Buffered into AgentTrace
# rows by ``build_rejection_for_call`` once all 4 LLM calls complete.
_TraceRecorder = Callable[[str, int, str], None]


REJECTION_THRESHOLD = 0.7  # Below this score-fraction → auto-rejection


# Mirrors backend/app/rejections_routes.py REJECTION_CATEGORIES + the
# alembic CHECK constraint. Do not add values here without updating both.
W4_CATEGORIES = (
    "ADMIN_ERROR",
    "PROCESS_FAILURE",
    "VERBAL_SALES_ERROR",
    "COMPLIANCE_ISSUE",
    "COMPLIANCE_ERROR",
    "PRICING_ISSUE",
    "PRICING_ERROR",
    "DOCUSIGN_ERROR",
    "FAILED_CREDIT_CHECK",
)


# Mirrors `remediation_action` Postgres enum from alembic b1d4f7e2c903 +
# WATT_REMEDIATION_ACTIONS in checkpoint_analyzer.py. Rejection.fix_required
# is constrained to one of these — free-text prose violates the enum so the
# factory's _propose_fix has to pick an enum, not write a sentence.
REMEDIATION_ACTIONS = (
    "AMENDMENT_CALL",
    "CONFIRMATION_CALL",
    "NEW_LOA",
    "NEW_DOCUSIGN",
    "DD_MANDATE",
    "RESELL_TO_OTHER_SUPPLIER",
    "PRICE_RECHECK",
    "COT_CHANGE_OF_TENANCY",
    "CONTRACT_LENGTH_LIMIT",
    "MANUAL_ADMIN_SUBMISSION",
)


def should_create_rejection(*, score: int | None, total: int | None) -> bool:
    if not total or total <= 0 or score is None:
        return False
    return (score / total) < REJECTION_THRESHOLD


def _format_failing(cps: list[dict]) -> str:
    lines = []
    for cp in cps:
        nm = cp.get("name", "")
        ev = (cp.get("evidence") or "").strip()
        nt = (cp.get("notes") or "").strip()
        st = cp.get("status", "")
        line = f"- [{st}] {nm}"
        if ev:
            line += f" — evidence: {ev[:200]}"
        if nt:
            line += f" — notes: {nt[:200]}"
        lines.append(line)
    return "\n".join(lines)


_CLASSIFY_PROMPT = """Pick the single best category for this call's compliance failures.

Category definitions:
ADMIN_ERROR — admin/data-entry mistake (wrong MPAN, wrong CED date, missing details on form)
PROCESS_FAILURE — required step skipped (no DPA read, no needs-analysis, no recording started)
VERBAL_SALES_ERROR — agent said wrong thing on call (wrong rate, wrong term, wrong supplier name, mis-stated unit price)
COMPLIANCE_ISSUE — soft regulatory breach, customer-facing risk (unclear consent, weak disclosure, ambiguous statement)
COMPLIANCE_ERROR — hard regulatory breach, regulator-actionable (no consent at all, misrepresentation, customer not informed)
PRICING_ISSUE — pricing logic concern needing review (margin question, customer dispute, recheck needed)
PRICING_ERROR — concrete wrong price quoted vs supplier (rate mismatch, locked-in wrong unit rate, wrong standing charge)
DOCUSIGN_ERROR — envelope problem (wrong signer, expired link, wrong details on document, CED wrong on doc)
FAILED_CREDIT_CHECK — supplier rejected on credit grounds

Examples:

Failing checkpoints:
- DPA confirmation read: fail (NOT FOUND IN TRANSCRIPT)
- Needs-analysis question asked: fail (NOT FOUND IN TRANSCRIPT)
→ PROCESS_FAILURE

Failing checkpoints:
- Unit rate stated correctly: fail ("28p per kWh" but supplier rate is 31p)
- Standing charge stated correctly: fail ("zero pence per day" but supplier charges 38p/day)
→ PRICING_ERROR

Failing checkpoints:
- Company name stated at start: fail (NOT FOUND IN TRANSCRIPT)
- DPA confirmation read: fail
- Customer affirmed consent to record: fail
→ COMPLIANCE_ERROR

Failing checkpoints:
{cps}

Output one category enum on a single line. No JSON, no prose."""


_REASON_PROMPT = """Write a 1-line rejection reason summarising why this call failed.
Quote the customer-facing problem in plain English. Aim for 80-160 chars.

Failing checkpoints:
{cps}

Output one sentence on a single line."""


_FIX_PROMPT = """Pick the single best remediation action for this rejection.

Allowed values (output exactly one):
AMENDMENT_CALL — call customer back to amend a single statement on the recording
CONFIRMATION_CALL — call customer back to re-confirm consent / a yes-response
NEW_LOA — send + collect a fresh Letter of Authority
NEW_DOCUSIGN — send a new DocuSign envelope and re-sign
DD_MANDATE — collect a Direct Debit mandate / bank details
RESELL_TO_OTHER_SUPPLIER — re-quote the customer onto a different supplier
PRICE_RECHECK — re-pull and re-confirm pricing with the supplier
COT_CHANGE_OF_TENANCY — process a change-of-tenancy event
CONTRACT_LENGTH_LIMIT — trim the contract length to satisfy supplier rules
MANUAL_ADMIN_SUBMISSION — admin types submission directly into the supplier portal

Failing checkpoints:
{cps}

Output one action enum on a single line. No JSON, no prose."""


_NARRATIVE_PROMPT = """Write a 1-sentence corrective action narrative for the
reviewer (free-text, NOT an enum). Aim for 80-160 chars.

Failing checkpoints:
{cps}

Output one sentence on a single line."""


async def _llm_call_with_trace(
    prompt: str,
    record_trace: Optional[_TraceRecorder],
    *,
    timeout: float = 15.0,
) -> str:
    """Wrap _call_llm with latency capture + optional trace recording."""
    started = time.perf_counter()
    out = await _call_llm(prompt, timeout=timeout)
    latency_ms = int((time.perf_counter() - started) * 1000)
    if record_trace is not None:
        try:
            record_trace(out, latency_ms, prompt)
        except Exception as e:
            log.warning(f"rejection_factory: trace recorder failed: {e}")
    return out


async def _classify_category(formatted_cps: str, record_trace: Optional[_TraceRecorder] = None) -> str:
    try:
        out = await _llm_call_with_trace(
            _CLASSIFY_PROMPT.replace("{cps}", formatted_cps), record_trace
        )
        cat = out.strip().strip('"').upper().split()[0] if out.strip() else ""
        if cat in W4_CATEGORIES:
            return cat
        log.warning(f"rejection_factory: classifier returned unknown category {cat!r}")
    except Exception as e:
        log.warning(f"rejection_factory: classify failed: {e}")
    return "ADMIN_ERROR"  # safe default — matches legacy fallback in rejections_routes._auto_classify


async def _summarise_reason(formatted_cps: str, record_trace: Optional[_TraceRecorder] = None) -> str:
    try:
        out = await _llm_call_with_trace(
            _REASON_PROMPT.replace("{cps}", formatted_cps), record_trace
        )
        return out.strip().strip('"')[:300]
    except Exception as e:
        log.warning(f"rejection_factory: summarise failed: {e}")
        return ""


async def _propose_fix(formatted_cps: str, record_trace: Optional[_TraceRecorder] = None) -> str | None:
    """Pick a remediation_action enum. None when nothing matches."""
    try:
        out = await _llm_call_with_trace(
            _FIX_PROMPT.replace("{cps}", formatted_cps), record_trace
        )
        action = out.strip().strip('"').upper().split()[0] if out.strip() else ""
        if action in REMEDIATION_ACTIONS:
            return action
        log.warning(f"rejection_factory: classifier returned unknown action {action!r}")
    except Exception as e:
        log.warning(f"rejection_factory: fix failed: {e}")
    return None  # column is nullable — leave blank rather than IntegrityError


async def _propose_narrative(formatted_cps: str, record_trace: Optional[_TraceRecorder] = None) -> str:
    """Free-text 1-sentence corrective-action narrative for the reviewer."""
    try:
        out = await _llm_call_with_trace(
            _NARRATIVE_PROMPT.replace("{cps}", formatted_cps), record_trace
        )
        return out.strip().strip('"')[:300]
    except Exception as e:
        log.warning(f"rejection_factory: narrative failed: {e}")
        return ""


async def build_rejection_for_call(
    *,
    call_id: str,
    customer_slug: str | None,
    supplier: str | None,
    sales_agent: str | None,
    failing_checkpoints: list[dict],
    db=None,
) -> dict:
    """Return a dict ready to feed into Rejection(**dict).

    Caller persists. Dict keys match Rejection model column names so the
    caller can ``Rejection(**out)`` directly. ``status`` defaults to
    ``NOT_STARTED`` to match the SQLAlchemy server_default and the
    REJECTION_STATUSES vocabulary.

    When ``db`` is provided, each of the 4 LLM calls (classify_category,
    summarise_reason, propose_fix, propose_narrative) writes one row into
    ``agent_traces`` so the HITL UI can render the classifier reasoning
    chain alongside the rejection. Trace persist failures never break the
    verdict — same swallow-and-log policy as ``checkpoint_analyzer``.
    """
    formatted = _format_failing(failing_checkpoints)

    run_id = str(uuid.uuid4())
    _trace_buffer: list[tuple[str, str, int, str]] = []  # (step, raw_out, ms, prompt)

    def _make_recorder(step: str) -> _TraceRecorder:
        def _r(raw_out: str, latency_ms: int, prompt: str) -> None:
            _trace_buffer.append((step, raw_out, latency_ms, prompt))
        return _r

    record_for = (
        (lambda step: _make_recorder(step)) if db is not None else (lambda step: None)
    )

    category = await _classify_category(formatted, record_for("classify_category"))
    reason = await _summarise_reason(formatted, record_for("summarise_reason"))
    fix = await _propose_fix(formatted, record_for("propose_fix"))
    narrative = await _propose_narrative(formatted, record_for("propose_narrative"))
    log.info(
        f"\U0001f6a9 REJECTION_BUILD call_id={call_id} category={category} "
        f"fix={fix} reason={reason!r}"
    )

    if db is not None and _trace_buffer:
        try:
            from app.models import AgentTrace
            rows: list[AgentTrace] = []
            for turn_idx, (step, raw_out, latency_ms, prompt) in enumerate(_trace_buffer):
                rows.append(AgentTrace(
                    id=str(uuid.uuid4()),
                    call_id=call_id,
                    checkpoint_id=None,
                    run_id=run_id,
                    turn=turn_idx * 2,
                    role="user",
                    tool_name=step,
                    content=prompt,
                    latency_ms=None,
                ))
                rows.append(AgentTrace(
                    id=str(uuid.uuid4()),
                    call_id=call_id,
                    checkpoint_id=None,
                    run_id=run_id,
                    turn=turn_idx * 2 + 1,
                    role="assistant",
                    tool_name=step,
                    content=raw_out,
                    latency_ms=latency_ms,
                ))
            db.add_all(rows)
            db.commit()
        except Exception as e:
            log.warning(f"rejection_factory: agent_trace persist failed run_id={run_id}: {e}")
            try:
                db.rollback()
            except Exception:
                pass
    payload: dict = {
        "call_id": call_id,
        "customer_slug": customer_slug,
        "supplier": supplier,
        "sales_agent": sales_agent,
        "category": category,
        "rejection_reason": reason,
        "status": "NOT_STARTED",
        # Auto-categorized by LLM, awaiting human review/confirmation.
        "verdict_state": "AI_PENDING",
    }
    if fix is not None:
        payload["fix_required"] = fix
    if narrative:
        # _propose_narrative produces a 1-sentence corrective-action narrative
        # describing what the reviewer/admin should do — that's a fix narrative,
        # not a terminal-state outcome narrative. outcome_narrative stays
        # nullable for the reviewer to fill at close-out time.
        payload["fix_narrative"] = narrative
    return payload
