"""OpenAI-format tool schemas + dispatcher for the agent loop.

Tool schemas follow the OpenAI function-calling format, which OpenRouter
supports natively across Claude, Gemini, GPT-4/5, and Mistral models.
"""
from typing import Any

from app.agent import tool_handlers
from app.agent.tool_handlers import ToolContext


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "find_evidence",
            "description": (
                "Fuzzy-match a short query string against the call transcript. "
                "Returns similarity score (0-1), whether it passed the verification "
                "threshold (0.75), and the best-matching section of transcript. "
                "Use this before deciding a checkpoint status."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Short phrase to search for, e.g. '30 pence per day'",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "verify_quote",
            "description": "Strict case-insensitive substring check. Use when you need exact match.",
            "parameters": {
                "type": "object",
                "properties": {"quote": {"type": "string"}},
                "required": ["quote"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_speaker",
            "description": (
                "Verify which speaker said a quote. Essential for customer_yes "
                "checkpoints — you must confirm the CUSTOMER (not the agent) "
                "gave the affirmative response."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "quote": {"type": "string", "description": "The quote to locate"},
                    "expected": {
                        "type": "string",
                        "enum": ["Agent", "Customer"],
                        "description": "Who you think said this",
                    },
                },
                "required": ["quote", "expected"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_word_context",
            "description": "Pull words in a time window around a position. Useful when you want to see what was said just before/after a specific moment.",
            "parameters": {
                "type": "object",
                "properties": {
                    "position": {"type": "number", "description": "Time in seconds"},
                    "window_seconds": {"type": "number", "default": 3.0},
                },
                "required": ["position"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "flag_low_confidence",
            "description": (
                "Mark a checkpoint as needing human review. Call this when you are "
                "less than 70% confident in your verdict."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "checkpoint": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["checkpoint", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_similar_learnings",
            "description": (
                "Semantic search over anonymized past human corrections (pgvector "
                "cosine similarity over embeddings of the learning pattern). Use "
                "this to check 'have reviewers corrected a similar case before?'. "
                "Pass a free-form `query` describing the situation; results come "
                "back ordered by relevance, not recency. You may also pass "
                "supplier+checkpoint_name as an exact-match fallback."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Free-form description of what to find similar cases for",
                    },
                    "supplier": {"type": "string"},
                    "checkpoint_name": {"type": "string"},
                    "limit": {"type": "integer", "default": 5},
                },
                "required": [],
            },
        },
    },
]


_HANDLER_MAP = {
    "find_evidence": tool_handlers.find_evidence,
    "verify_quote": tool_handlers.verify_quote,
    "check_speaker": tool_handlers.check_speaker,
    "get_word_context": tool_handlers.get_word_context,
    "flag_low_confidence": tool_handlers.flag_low_confidence,
    "get_similar_learnings": tool_handlers.get_similar_learnings,
}


def dispatch_tool(ctx: ToolContext, *, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Route a tool call from the LLM to the right handler. Returns handler output or error dict."""
    handler = _HANDLER_MAP.get(name)
    if handler is None:
        return {"error": f"unknown tool: {name}"}
    try:
        return handler(ctx, **arguments)
    except TypeError as e:
        return {"error": f"bad arguments for {name}: {e}"}
    except Exception as e:
        return {"error": f"tool {name} raised: {type(e).__name__}: {e}"}
