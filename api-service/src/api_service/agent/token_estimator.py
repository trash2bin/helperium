"""Token estimation utilities for LLM context window management.

Fast heuristic token counting (chars / 3.5 ≈ average for mixed
Cyrillic/Latin text) and message trimming for fallback scenarios.

These are pure functions — no dependencies on the agent runtime.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("api_service.agent.token_estimator")


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """Fast heuristic token estimate: chars / 3.5 (≈ рус/eng average).

    This is NOT a precise tokenizer — it's a guard against blowing past
    the model's context window.  8000 tokens ≈ 28000 chars.

    The system prompt is embedded in messages, so its cost is included.
    """
    if not messages:
        return 0

    total_chars = sum(
        len(json.dumps(m, ensure_ascii=False, default=str)) for m in messages
    )
    return int(total_chars / 3.5)


def trim_for_fallback(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Trim messages for fallback: system prompt + last 2 exchanges.

    In a fallback scenario the model has already exhausted its context
    window — resending all messages repeats the failure.  A fresh short
    slice gives the model a chance to produce a clean response.

    Exchange = a group of user / assistant / tool / system messages.
    """
    if len(messages) <= 3:
        return list(messages)

    # First element is always the system prompt — keep it.
    sys_msg = messages[0]
    # Last 4 messages (~2 exchanges with reminders).
    tail = messages[-4:]
    return [sys_msg] + list(tail)
