"""L6 internal-only Claude tool-use loop.

Used by the L4 /agents/[name] similar-failure cluster panel and reserved
for the post-demo chat UI revival. **No public chat endpoint** — call
`run_chat(...)` directly from a backend route or test.

Streams tokens via OpenAI-format SSE deltas (yielded as ('token', text)
events). When the model emits tool_calls, we execute against
`app.agent.rag_tools.dispatch` and feed the result back as a tool message.
Hard cap at 10 iterations to avoid runaway loops.

OpenRouter's Chat Completions endpoint is OpenAI-compatible — we use the
official `openai` SDK pointed at https://openrouter.ai/api/v1.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, AsyncIterator

from sqlalchemy.orm import Session

from app.agent import rag_tools

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "anthropic/claude-opus-4.7"
MAX_ITERATIONS = 10


def _build_openai_tools() -> list[dict[str, Any]]:
    """Convert the Anthropic-shaped schemas to OpenAI function-calling shape."""
    out: list[dict[str, Any]] = []
    for t in rag_tools.TOOL_SCHEMAS:
        out.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        })
    return out


def _get_client():
    """Lazy OpenRouter (OpenAI-compatible) client. Raises if key missing."""
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise EnvironmentError("OPENROUTER_API_KEY not set — agent chat unavailable")
    from openai import OpenAI  # lazy

    return OpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")


async def run_chat(
    messages: list[dict[str, Any]],
    db: Session,
    *,
    model: str = DEFAULT_MODEL,
    client: Any = None,
) -> AsyncIterator[tuple[str, Any]]:
    """Run the tool-use loop. Yields ('token', str) and ('tool_call', dict) events.

    `client` may be injected for tests; defaults to a real OpenRouter client.
    """
    if client is None:
        client = _get_client()

    tools = _build_openai_tools()
    convo: list[dict[str, Any]] = list(messages)

    for iteration in range(MAX_ITERATIONS):
        # Streaming completion. The OpenAI SDK returns an iterator of chunks.
        stream = client.chat.completions.create(
            model=model,
            messages=convo,
            tools=tools,
            stream=True,
        )

        accumulated_text = ""
        tool_calls_acc: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None

        for chunk in stream:
            choice = chunk.choices[0] if chunk.choices else None
            if choice is None:
                continue
            delta = choice.delta
            if getattr(delta, "content", None):
                accumulated_text += delta.content
                yield ("token", delta.content)
            tcs = getattr(delta, "tool_calls", None) or []
            for tc in tcs:
                idx = getattr(tc, "index", 0)
                slot = tool_calls_acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                if getattr(tc, "id", None):
                    slot["id"] = tc.id
                fn = getattr(tc, "function", None)
                if fn is not None:
                    if getattr(fn, "name", None):
                        slot["name"] = fn.name
                    if getattr(fn, "arguments", None):
                        slot["arguments"] += fn.arguments
            if getattr(choice, "finish_reason", None):
                finish_reason = choice.finish_reason

        if finish_reason == "tool_calls" and tool_calls_acc:
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": accumulated_text or None,
                "tool_calls": [
                    {
                        "id": tc["id"] or f"call_{i}",
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": tc["arguments"]},
                    }
                    for i, tc in sorted(tool_calls_acc.items())
                ],
            }
            convo.append(assistant_msg)
            for i, tc in sorted(tool_calls_acc.items()):
                try:
                    args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                except Exception:
                    args = {}
                yield ("tool_call", {"name": tc["name"], "arguments": args})
                result = rag_tools.dispatch(db, tc["name"], args)
                convo.append({
                    "role": "tool",
                    "tool_call_id": tc["id"] or f"call_{i}",
                    "content": json.dumps(result, default=str),
                })
            continue  # next iteration

        # Final answer (or stop with no tool call) — yield a 'done' marker.
        convo.append({"role": "assistant", "content": accumulated_text})
        yield ("done", {"text": accumulated_text, "finish_reason": finish_reason, "iterations": iteration + 1})
        return

    yield ("done", {"text": "", "finish_reason": "max_iterations", "iterations": MAX_ITERATIONS})
