"""Fallback handler — produces a final answer when the main loop exhausted
its iterations without a conclusive response.

The handler trims the message history to give the model a "fresh start"
instead of repeating the same failure, then streams a non-streaming LLM
call and saves the turn.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import cast

from .conversation import ConversationManager
from .llm_client import LLMClientProtocol
from .prompts import FALLBACK_GENERIC
from .token_estimator import trim_for_fallback
from .turn_context import TurnContext
from .types import (
    AgentEvent,
    FinalEventData,
    TurnMessages,
)

logger = logging.getLogger("api_service.agent.fallback_handler")


class FallbackHandler:
    """Run a trimmed fallback when the agent loop ends without a final answer."""

    def __init__(
        self,
        llm_client: LLMClientProtocol,
        conversation_manager: ConversationManager,
    ) -> None:
        self._llm = llm_client
        self._conv_mgr = conversation_manager

    async def run(
        self, ctx: TurnContext, *, was_finished: bool = False
    ) -> AsyncIterator[AgentEvent]:
        """Stream the fallback answer.

        Cuts ``ctx.messages`` to system prompt + last 2 exchanges,
        calls ``get_final_message()`` on the LLM, and yields tokens.
        """
        final_parts: list[str] = []
        fallback_messages = trim_for_fallback(ctx.messages)

        logger.info(
            "[FALLBACK] Trimming %d messages to %d for fallback",
            len(ctx.messages),
            len(fallback_messages),
        )

        async for token in self._llm.get_final_message(fallback_messages):
            final_parts.append(token)
            yield AgentEvent("token", {"data": token})

        if not final_parts and not was_finished:
            final_parts.append(FALLBACK_GENERIC)
            yield AgentEvent("token", {"data": FALLBACK_GENERIC})

        full_answer = "".join(final_parts)
        ctx.turn_messages.append(
            {
                "role": "assistant",
                "content": full_answer,
            }
        )
        await self._conv_mgr.aremember_turn(
            ctx.session_id,
            cast(TurnMessages, ctx.turn_messages),
        )

        yield AgentEvent("final", FinalEventData(content=full_answer))
