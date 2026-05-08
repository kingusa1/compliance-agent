"""Core single-batch agent loop.

Takes a batch of 6 checkpoints + transcript + tools, runs an
OpenAI-compatible tool-use conversation with the LLM, returns
verdicts in the same shape as _analyze_batch().
"""
import json
import logging
import re
import time
import uuid
from typing import Any

import httpx

from app.agent import tools as tool_registry
from app.agent.playbooks import load_combined_playbook
from app.agent.tool_handlers import ToolContext
from app.config import settings
from app.logger import log
from app.resilience import LLM_RETRY
from app.verification import fuzzy_match

logger = logging.getLogger(__name__)


def _build_system_prompt(supplier: str) -> str:
    return load_combined_playbook(supplier)


def _build_user_prompt(batch: list[dict], transcript: str) -> str:
    cp_lines = []
    for cp in batch:
        cp_lines.append(
            f"CHECKPOINT: {cp['name']}\n"
            f"  Required: {cp.get('required', '')}\n"
            f"  Key phrases: {', '.join(cp.get('key_phrases', []))}\n"
            f"  Strictness: {cp.get('strictness', 'mandatory')}\n"
        )
    checkpoints_text = "\n".join(cp_lines)

    return (
        "Analyze the following compliance checkpoints against the transcript. "
        "Use tools when you need to verify evidence, check speakers, or consult past learnings. "
        "Return ONLY a JSON array of verdicts — one object per checkpoint, same order as input.\n\n"
        f"CHECKPOINTS:\n{checkpoints_text}\n\n"
        f"TRANSCRIPT:\n{transcript}"
    )


@LLM_RETRY
async def _call_llm_with_tools(
    *,
    model: str,
    messages: list[dict],
    tools: list[dict],
    timeout: float = 90.0,
) -> dict:
    """Call OpenRouter chat completions with tools. Retries on transient httpx errors."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": messages,
                "tools": tools,
                "tool_choice": "auto",
                "temperature": 0,
                "max_tokens": 4096,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()


def _parse_verdicts(content: str) -> list[dict] | None:
    """Extract the JSON array of verdicts from a final assistant message."""
    if not content:
        return None
    text = content.strip()
    if text.startswith("```"):
        # Handle both ```json\n{...}``` and ```json{...}``` (no newline after fence)
        parts = text.split("\n", 1)
        if len(parts) > 1:
            text = parts[1].rsplit("```", 1)[0].strip()
        else:
            text = re.sub(r"^```\w*", "", parts[0]).rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict) and "checkpoints" in parsed:
            return parsed["checkpoints"]
    except (json.JSONDecodeError, IndexError, ValueError):
        return None
    return None


def _verify_verdict_evidence(transcript: str, verdict: dict) -> dict:
    """Run fuzzy_match on the verdict's evidence quote. Downgrade status if quote not found."""
    status = verdict.get("status", "fail")
    if status not in ("pass", "partial"):
        verdict["verified"] = True
        verdict["similarity"] = 1.0
        return verdict

    evidence = verdict.get("evidence", "")
    if not evidence or evidence == "NOT FOUND IN TRANSCRIPT":
        verdict["verified"] = True
        verdict["similarity"] = 1.0
        return verdict

    match = fuzzy_match(transcript, evidence)
    verdict["verified"] = match["verified"]
    verdict["similarity"] = match["similarity"]
    if not match["verified"]:
        verdict["status"] = "unverified"
        verdict["notes"] = (verdict.get("notes") or "") + f" [QUOTE NOT VERIFIED similarity={match['similarity']}]"
    return verdict


def _error_results(batch: list[dict], reason: str) -> list[dict]:
    return [
        {
            "section": cp.get("section", i + 1),
            "name": cp.get("name", f"Checkpoint {i+1}"),
            "status": "error",
            "evidence": f"Agent error: {reason}",
            "notes": reason,
            "confidence": "low",
            "needs_review": True,
            "agent_name": "Unknown",
            "customer_name": "Unknown",
            "verified": False,
            "similarity": 0,
        }
        for i, cp in enumerate(batch)
    ]


async def run_agent_on_batch(
    ctx: ToolContext,
    batch: list[dict],
    *,
    model: str,
    max_turns: int | None = None,
) -> list[dict]:
    """Run the agent loop for a single batch. Returns list[dict] matching the shape of _analyze_batch().

    When ctx.db and ctx.call_id are both set, the loop accumulates an
    `AgentTrace` row per turn (user prompt, assistant message, tool call,
    tool result) and flushes them in a single batched commit at the end so
    tracing doesn't block the hot path. If either is missing, tracing is
    skipped — tests that don't want the DB dependency don't have to set it up.
    """
    max_turns = max_turns or settings.agent_max_turns

    messages = [
        {"role": "system", "content": _build_system_prompt(ctx.supplier)},
        {"role": "user", "content": _build_user_prompt(batch, ctx.transcript)},
    ]
    flagged_checkpoints: dict[str, str] = {}

    # ─── Trace accumulator ────────────────────────────────────────────────
    # Shared across this whole batch-run so every row has the same run_id.
    # Checkpoint_id is only set for single-checkpoint runs (batch size 1) —
    # multi-checkpoint batches share the trace at the call level.
    tracing_enabled = ctx.db is not None and ctx.call_id is not None
    run_id = str(uuid.uuid4())
    cp_id_for_run = batch[0].get("id") if len(batch) == 1 else None
    traces: list[dict[str, Any]] = []
    turn_counter = 0

    def _record(
        role: str,
        *,
        content: str | None = None,
        tool_name: str | None = None,
        tool_input: Any = None,
        tool_output: Any = None,
        latency_ms: int | None = None,
    ) -> None:
        """Buffer one AgentTrace row. No DB I/O until the end of the run."""
        if not tracing_enabled:
            return
        nonlocal turn_counter
        traces.append({
            "role": role,
            "turn": turn_counter,
            "content": content,
            "tool_name": tool_name,
            "tool_input": json.dumps(tool_input) if tool_input is not None else None,
            "tool_output": json.dumps(tool_output) if tool_output is not None else None,
            "latency_ms": latency_ms,
        })
        turn_counter += 1

    # Seed turn 0 with the user prompt so the reviewer can see what the
    # agent was asked.
    _record("user", content=messages[-1]["content"])

    for turn in range(max_turns):
        t0 = time.monotonic()
        try:
            response = await _call_llm_with_tools(
                model=model,
                messages=messages,
                tools=tool_registry.TOOL_SCHEMAS,
            )
        except Exception as e:
            log.warning(f"🤖 AGENT llm call failed turn={turn}: {e}")
            _flush_traces(ctx, traces, run_id=run_id, checkpoint_id=cp_id_for_run, model=model)
            return _error_results(batch, f"llm call failed: {e}")
        llm_latency_ms = int((time.monotonic() - t0) * 1000)

        choices = response.get("choices") or []
        if not choices:
            err_detail = response.get("error", str(response)[:200])
            log.warning(f"🤖 AGENT no choices in response turn={turn}: {err_detail}")
            _flush_traces(ctx, traces, run_id=run_id, checkpoint_id=cp_id_for_run, model=model)
            return _error_results(batch, f"llm returned no choices: {err_detail}")

        msg = choices[0].get("message") or {}
        tool_calls = msg.get("tool_calls") or []

        usage = response.get("usage") or {}
        log.info(
            f"\U0001f916 AGENT turn={turn} model={model} "
            f"prompt_tokens={usage.get('prompt_tokens', 0)} "
            f"completion_tokens={usage.get('completion_tokens', 0)} "
            f"tool_calls={len(tool_calls)}"
        )

        # Record the assistant response (final OR tool-calling).
        _record(
            "assistant",
            content=msg.get("content"),
            latency_ms=llm_latency_ms,
        )

        if not tool_calls:
            content = msg.get("content", "")
            verdicts = _parse_verdicts(content)
            if verdicts is None:
                log.warning("🤖 AGENT returned non-JSON final message — marking batch error")
                _flush_traces(ctx, traces, run_id=run_id, checkpoint_id=cp_id_for_run, model=model)
                return _error_results(batch, "agent returned unparseable verdict JSON")
            _flush_traces(ctx, traces, run_id=run_id, checkpoint_id=cp_id_for_run, model=model)
            return _finalize_verdicts(ctx, batch, verdicts, flagged_checkpoints)

        # Append assistant message and execute each tool call
        messages.append({
            "role": "assistant",
            "content": msg.get("content"),
            "tool_calls": tool_calls,
        })

        for tc in tool_calls:
            name = tc["function"]["name"]
            raw_args = tc["function"].get("arguments", "")
            log.info(f"\U0001f527 AGENT tool_call name={name} args={str(raw_args)[:100]}")
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError:
                args = {}
            tt0 = time.monotonic()
            result = tool_registry.dispatch_tool(ctx, name=name, arguments=args)
            tool_latency_ms = int((time.monotonic() - tt0) * 1000)
            if name == "flag_low_confidence":
                cp_name = args.get("checkpoint", "")
                reason = args.get("reason", "low confidence")
                flagged_checkpoints[cp_name] = reason
            _record(
                "tool",
                tool_name=name,
                tool_input=args,
                tool_output=result,
                latency_ms=tool_latency_ms,
            )
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": json.dumps(result),
            })

    _flush_traces(ctx, traces, run_id=run_id, checkpoint_id=cp_id_for_run, model=model)
    return _error_results(batch, f"agent exceeded {max_turns} turns without final verdict")


def _flush_traces(
    ctx: ToolContext,
    traces: list[dict[str, Any]],
    *,
    run_id: str,
    checkpoint_id: str | None,
    model: str,
) -> None:
    """Persist buffered trace rows in a single commit.

    Silently swallows DB errors: a tracing failure must never propagate out
    and break the verdict. We log and move on so a schema drift or
    connection hiccup doesn't fail the whole pipeline.
    """
    if ctx.db is None or ctx.call_id is None or not traces:
        return
    try:
        from app.models import AgentTrace
        rows = [
            AgentTrace(
                id=str(uuid.uuid4()),
                call_id=ctx.call_id,
                checkpoint_id=checkpoint_id,
                run_id=run_id,
                turn=t["turn"],
                role=t["role"],
                tool_name=t.get("tool_name"),
                tool_input=t.get("tool_input"),
                tool_output=t.get("tool_output"),
                content=t.get("content"),
                model=model,
                latency_ms=t.get("latency_ms"),
            )
            for t in traces
        ]
        ctx.db.add_all(rows)
        ctx.db.commit()
    except Exception as e:
        log.warning(f"🤖 AGENT trace persist failed run_id={run_id}: {e}")
        try:
            ctx.db.rollback()
        except Exception:
            pass


def _finalize_verdicts(
    ctx: ToolContext,
    batch: list[dict],
    raw_verdicts: list[dict],
    flagged: dict[str, str],
) -> list[dict]:
    """Post-process raw verdicts: verify evidence, apply needs_review, align with batch order."""
    results = []
    for i, cp in enumerate(batch):
        # Find verdict for this checkpoint by name (fall back to positional)
        match = next((v for v in raw_verdicts if v.get("name") == cp["name"]), None)
        if match is None and i < len(raw_verdicts):
            match = raw_verdicts[i]
        if match is None:
            results.append(_error_results([cp], "no verdict returned for this checkpoint")[0])
            continue

        verified = _verify_verdict_evidence(ctx.transcript, dict(match))

        confidence = verified.get("confidence", "high")
        needs_review = confidence == "low" or cp["name"] in flagged

        results.append({
            "section": cp.get("section", i + 1),
            "name": cp["name"],
            "status": verified.get("status", "fail"),
            "evidence": verified.get("evidence", ""),
            "notes": verified.get("notes") or flagged.get(cp["name"]),
            "confidence": confidence,
            "needs_review": needs_review,
            "agent_name": verified.get("agent_name", "Unknown"),
            "customer_name": verified.get("customer_name", "Unknown"),
            "verified": verified.get("verified", False),
            "similarity": verified.get("similarity", 0),
        })
    return results
