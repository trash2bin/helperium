"""Token estimation utilities for LLM context window management.

Fast approximate (tiktoken via LiteLLM) token counting and message trimming
for fallback scenarios.
"""

from __future__ import annotations

import logging
from typing import Any

import litellm
from helperium_sdk.settings import settings

logger = logging.getLogger("api_service.agent.token_estimator")


def estimate_tokens(messages: list[dict[str, Any]], model: str = "") -> int:
    """Token count via LiteLLM/tiktoken, with per-model tokenizer.

    Uses ``litellm.token_counter()`` which selects the right tiktoken
    encoder for the given model.  Falls back to a safe default when
    ``model`` is unknown or empty (cl100k_base, same as GPT-4/Claude).

    Args:
        messages: List of message dicts (system + user + assistant + tool).
        model: Model identifier (e.g. ``"gpt-4o"``, ``"anthropic/claude-..."``).
               Empty string uses the default tokenizer (cl100k_base).

    Returns:
        Token count (int).  0 for empty input.
    """
    if not messages:
        return 0

    try:
        return litellm.token_counter(model=model, messages=messages)
    except Exception as exc:
        logger.warning(
            "token_counter failed for model=%r: %s — using chars/3.5 fallback",
            model,
            exc,
        )
        total_chars = sum(
            len(str(m.get("content", "")))
            + len(str(m.get("tool_calls", "")))
            + len(str(m.get("name", "")))
            for m in messages
        )
        return int(total_chars / 3.5)


def trim_for_fallback(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Trim messages for fallback: system + last user + recent context.

    In a fallback scenario the model has already exhausted its context
    window — resending all messages repeats the failure.  A fresh short
    slice gives the model a chance to produce a clean response.

    ALWAYS keeps the last user message so the model knows what the user
    originally asked — that context is the most critical.

    Strategy: find the last user message, take EVERYTHING from it to end
    (user + assistant replies + tool results). Trim excess from the
    END (tool results), never from the beginning (user question).
    """
    if len(messages) <= 3:
        return list(messages)

    # First element is always the system prompt — keep it.
    sys_msg = messages[0]

    # Find the last user message — the original question must survive.
    last_user_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            last_user_idx = i
            break

    # Tail: from last user through end. Cap to max_fallback_messages from the START
    # (user first, then as many tool results as fit).
    max_msgs = settings.agent_fallback_max_messages
    if last_user_idx >= 0:
        tail = messages[last_user_idx:]
        if len(tail) > max_msgs:
            tail = tail[:max_msgs]  # ← user на 1й позиции, не теряем!
    else:
        tail = messages[-4:]

    return [sys_msg] + list(tail)
