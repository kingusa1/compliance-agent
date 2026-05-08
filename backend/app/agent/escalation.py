"""Tiered model escalation: run Gemini Flash first, re-run low-confidence with Sonnet.

At 500 calls/day this saves roughly 20x vs always using Sonnet, while
keeping Sonnet's accuracy on the 20% of cases that actually need it.
"""
import time as _time

from app.agent.agent_loop import run_agent_on_batch
from app.agent.tool_handlers import ToolContext
from app.config import settings
from app.logger import log
from app.observability_metrics import record_llm_call


async def run_batch_tiered(ctx: ToolContext, batch: list[dict]) -> list[dict]:
    """Run first pass with fast model; re-run low-confidence checkpoints with escalation model."""
    # Step 1 — fast first pass
    _t0 = _time.monotonic()
    try:
        first_pass = await run_agent_on_batch(ctx, batch, model=settings.gemini_flash_model)
    finally:
        record_llm_call(
            model=settings.gemini_flash_model,
            duration_seconds=_time.monotonic() - _t0,
            escalated=False,
        )

    # Mark every result with escalated=False by default
    for r in first_pass:
        r["escalated"] = False

    # Step 2 — find checkpoints whose confidence is below threshold
    low_confidence_names = {
        r["name"] for r in first_pass
        if r.get("confidence") == settings.agent_escalation_threshold
        or r.get("status") == "error"
    }

    if not low_confidence_names:
        log.info(f"\U0001f3af AGENT tier=fast_only batch_size={len(batch)} model={settings.gemini_flash_model}")
        return first_pass

    log.info(
        f"\u2b06\ufe0f AGENT tier=escalated batch_size={len(batch)} "
        f"escalated={len(low_confidence_names)} "
        f"fast_model={settings.gemini_flash_model} "
        f"esc_model={settings.agent_escalation_model}"
    )

    to_rerun = [cp for cp in batch if cp["name"] in low_confidence_names]
    _t0 = _time.monotonic()
    try:
        escalated = await run_agent_on_batch(
            ctx, to_rerun, model=settings.agent_escalation_model,
        )
    finally:
        record_llm_call(
            model=settings.agent_escalation_model,
            duration_seconds=_time.monotonic() - _t0,
            escalated=True,
        )

    # Merge: replace first-pass results with escalated results for re-run checkpoints
    escalated_by_name = {r["name"]: r for r in escalated}
    merged = []
    for r in first_pass:
        if r["name"] in escalated_by_name:
            esc = escalated_by_name[r["name"]]
            esc["escalated"] = True
            merged.append(esc)
        else:
            merged.append(r)
    return merged
