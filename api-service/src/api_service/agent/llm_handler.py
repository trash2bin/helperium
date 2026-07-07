"""LLM interaction handler — single responsibility: call the model.

``LLMHandler`` owns the streaming call to the LLM and the parsing of its
response into the three possible outcomes recorded on ``TurnContext``:

* ``outcome="tool_calls"`` with ``pending_tool_calls`` populated
* ``outcome="final"`` with ``is_finished=True``
* ``outcome="empty_round"`` with incremented ``empty_rounds``

It does NOT handle tool execution (that's ``ToolHandler``) or
fallback logic (that's ``FallbackHandler``).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from .llm_client import LLMClientProtocol
from .prompts import PARTIAL_REMINDER
from .tool_parser import ToolCallParser
from .turn_context import TurnContext
from .types import AgentEvent, TokenEventData

logger = logging.getLogger("api_service.agent.llm_handler")


class LLMHandler:
    """Call the LLM, stream tokens, and record the outcome on TurnContext.

    Yields ``token`` events only.  After the generator finishes the caller
    should inspect ``ctx.outcome`` to decide what to do next.
    """

    def __init__(
        self,
        llm_client: LLMClientProtocol,
        tool_parser: ToolCallParser | None = None,
    ) -> None:
        self._llm = llm_client
        self._tool_parser = tool_parser or ToolCallParser()

    async def stream_and_parse(self, ctx: TurnContext) -> AsyncIterator[AgentEvent]:
        """Stream the LLM response and set outcome fields on ``ctx``.

        Yields ``token`` events.

        Post-conditions on ``ctx``:

        * ``outcome="tool_calls"`` → ``pending_tool_calls`` is populated,
          assistant message with tool_calls appended to ``ctx.messages``.
        * ``outcome="final"`` → ``is_finished=True``, content saved to
          ``ctx.messages`` / ``ctx.turn_messages``.
        * ``outcome="empty_round"`` → ``empty_rounds`` incremented,
          reasoning / reminder injected into ``ctx.messages``.
        * ``outcome=None`` → model returned nothing at all (rare).
        """
        logger.info(
            "[LLM_HANDLER] Calling model (iteration %d, %d tools)...",
            ctx.iteration + 1,
            len(ctx.tools),
        )

        # ── Reset outcome ───────────────────────────────────────────────
        ctx.outcome = None
        ctx.pending_tool_calls = []

        # ── Stream tokens + collect final message ───────────────────────
        final_message: dict[str, Any] | None = None

        async for token, final in self._llm.stream_completion(
            ctx.messages,
            tools=ctx.tools if ctx.tools else None,
        ):
            if token:
                yield AgentEvent("token", TokenEventData(data=token))
            elif final:
                final_message = final
                break  # final message received, no more tokens

        # Try fallback to last_final_message if stream produced nothing.
        if final_message is None:
            final_message = self._llm.last_final_message

        # ── 1. Empty response ───────────────────────────────────────────
        if final_message is None:
            logger.warning("[LLM_HANDLER] Empty response (iteration %d)", ctx.iteration)
            ctx.empty_rounds += 1
            ctx.outcome = "empty_round"
            return

        # ── Strip internal usage metadata ───────────────────────────────
        final_message.pop("_usage", None)

        # ── Extract components ──────────────────────────────────────────
        reasoning = final_message.get("reasoning_content")
        tool_calls = self._tool_parser.extract_tool_calls(final_message)
        content = (final_message.get("content") or "").strip()

        logger.info(
            "[LLM_HANDLER] Reasoning=%s, ToolCalls=%d, Content=%d chars",
            bool(reasoning),
            len(tool_calls),
            len(content),
        )

        # Log reasoning content for debugging
        if reasoning:
            logger.debug("[LLM_HANDLER] Reasoning:\n%s", reasoning)

        # ── 2. Tool calls ───────────────────────────────────────────────
        if tool_calls:
            logger.info("[LLM_HANDLER] Outcome: tool_calls (%d)", len(tool_calls))
            # Append assistant message with tool_calls to message history.
            final_message["tool_calls"] = self._tool_parser.format_for_model(tool_calls)
            ctx.messages.append(final_message)
            ctx.turn_messages.append(final_message)

            ctx.outcome = "tool_calls"
            ctx.pending_tool_calls = tool_calls
            return

        # ── 3. Final content ────────────────────────────────────────────
        if content:
            logger.info("[LLM_HANDLER] Outcome: final (%d chars)", len(content))
            final_message["content"] = content
            ctx.messages.append(final_message)
            ctx.turn_messages.append(final_message)
            ctx.is_finished = True
            ctx.outcome = "final"
            return

        # ── 4. Partial / reasoning-only response ────────────────────────
        logger.info(
            "[LLM_HANDLER] Outcome: empty_round (reasoning only=%s)",
            bool(reasoning),
        )
        ctx.empty_rounds += 1
        ctx.outcome = "empty_round"

        if reasoning:
            ctx.messages.append(
                {
                    "role": "assistant",
                    "content": reasoning,
                }
            )

        ctx.messages.append(
            {
                "role": "system",
                "content": PARTIAL_REMINDER,
            }
        )
