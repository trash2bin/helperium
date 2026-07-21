"""Turn-level state container for the LLM agent loop.

``TurnContext`` holds every piece of mutable state that flows through
a single conversation turn — messages, iteration counters, tool list.
It eliminates the parameter soup that previously travelled through
four levels of private methods in the orchestrator.

Usage::

    ctx = await TurnContext.build(
        user_message="hello",
        session_id="sess-1",
        system_prompt="You are …",
        conversation_manager=conv_mgr,
    )
    # … later in the loop …
    ctx.iteration = 2
    ctx.messages.append(assistant_reply)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .conversation import ConversationManager
from .types import SessionId


@dataclass
class TurnContext:
    """Mutable container for all turn-level state.

    Create via ``TurnContext.build()``.  Mutate fields in-place as the
    agent loop progresses.

    Fields prefixed with ``_`` are orchestration state managed by
    handlers and should not be set directly by callers.
    """

    # ── Message lists ──────────────────────────────────────────────────────
    # Full message history for this turn (system + history + user +
    # assistant replies + tool results).
    messages: list[dict[str, Any]] = field(default_factory=list)

    # Messages produced THIS turn only (saved to conversation store at end).
    turn_messages: list[dict[str, Any]] = field(default_factory=list)

    # ── Identifiers ───────────────────────────────────────────────────────────
    session_id: SessionId = ""
    turn_id: str = ""
    tenant_ids: list[str] | None = None

    # ── MCP tool definitions for the current tenant(s) ──────────────────────
    tools: list[dict[str, Any]] = field(default_factory=list)

    # ── Loop state ──────────────────────────────────────────────────────────
    iteration: int = 0
    empty_rounds: int = 0

    # ── Pending tool calls ─────────────────────────────────────────────────
    pending_calls: list[dict] = field(default_factory=list)
    """Tool calls pending execution from the last LLM response."""

    # ── Tool results accumulator ────────────────────────────────────────────
    tool_results: list[dict] = field(default_factory=list)
    """Accumulated tool call results for this turn."""

    final_content: str = ""
    """Final assistant content once the turn completes."""

    # ── Factory ─────────────────────────────────────────────────────────────

    @staticmethod
    async def build(
        user_message: str,
        session_id: SessionId,
        system_prompt: str,
        conversation_manager: ConversationManager,
        tenant_ids: list[str] | None = None,
    ) -> TurnContext:
        """Build a context from scratch: load history, prepend system prompt.

        Args:
            user_message:  Raw text from the user.
            session_id:    Current conversation session.
            system_prompt: Agent system prompt (may be per-agent override).
            conversation_manager: Loads persisted history.
            tenant_ids:    Optional tenant IDs for cost attribution.

        Returns:
            A fully initialised TurnContext ready for the agent loop.
        """
        history: list[
            dict[str, Any]
        ] = await conversation_manager.aget_history_messages(session_id)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            *history,
            {"role": "user", "content": user_message},
        ]

        turn_messages: list[dict[str, Any]] = [
            {"role": "user", "content": user_message},
        ]

        return TurnContext(
            messages=messages,
            turn_messages=turn_messages,
            session_id=session_id,
            tenant_ids=tenant_ids,
        )
