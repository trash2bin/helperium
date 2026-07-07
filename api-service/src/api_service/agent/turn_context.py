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
from typing import Any, Literal

from .conversation import ConversationManager
from .types import ParsedToolCall, SessionId


Outcome = Literal["tool_calls", "final", "empty_round", "unknown"] | None


@dataclass
class TurnContext:
    """Mutable container for all turn-level state.

    Create via ``TurnContext.build()``.  Mutate fields in-place as the
    agent loop progresses.
    """

    # ── Message lists ──────────────────────────────────────────────────────
    # Full message history for this turn (system + history + user +
    # assistant replies + tool results).
    messages: list[dict[str, Any]] = field(default_factory=list)

    # Messages produced THIS turn only (saved to conversation store at end).
    turn_messages: list[dict[str, Any]] = field(default_factory=list)

    # ── Identifiers ──────────��──────────────────────────────────────────────
    session_id: SessionId = ""
    turn_id: str = ""

    # ── MCP tool definitions for the current tenant(s) ──────────────────────
    tools: list[dict[str, Any]] = field(default_factory=list)

    # ── Loop state ──────────────────────────────────────────────────────────
    iteration: int = 0
    empty_rounds: int = 0
    is_finished: bool = False  # set by LLMHandler when final content arrives

    # ── Dispatch info (set by LLMHandler after streaming, read by orchestrator)
    outcome: Outcome = None
    """What happened in the last LLM call, used by orchestrator to decide
    what to do next: execute tools, save final answer, or loop again."""

    pending_tool_calls: list[ParsedToolCall] = field(default_factory=list)
    """Tool calls extracted by LLMHandler, ready for ToolHandler to execute."""

    # ── Factory ─────────────────────────────────────────────────────────────

    @staticmethod
    async def build(
        user_message: str,
        session_id: SessionId,
        system_prompt: str,
        conversation_manager: ConversationManager,
    ) -> TurnContext:
        """Build a context from scratch: load history, prepend system prompt.

        Args:
            user_message:  Raw text from the user.
            session_id:    Current conversation session.
            system_prompt: Agent system prompt (may be per-agent override).
            conversation_manager: Loads persisted history.

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
        )
