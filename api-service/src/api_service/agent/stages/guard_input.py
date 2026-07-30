"""Stage 1 — Guard Input.

Проверка входящего сообщения на prompt injection.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from ..pipeline import PipelineContext
from ..types import AgentEvent, ErrorEventData

logger = logging.getLogger("api_service.agent.stages")


class GuardInputStage:
    """Проверка входящего сообщения на prompt injection.

    Выполняется один раз в начале pipeline.
    При блокировке выставляет ``ctx.should_stop = True``.
    """

    async def run(self, ctx: PipelineContext) -> AsyncIterator[AgentEvent]:
        if False:
            yield  # pragma: no cover — make Python treat this as async generator

        if ctx._stage_ran("guard_input"):
            return
        ctx._mark_done("guard_input")

        user_message = (
            ctx.turn.turn_messages[0].get("content", "")
            if ctx.turn.turn_messages
            else ""
        )

        guard_reason = ""
        if ctx.guard_checker is not None:
            guard_result = ctx.guard_checker.check_input(user_message)
            if guard_result.blocked:
                guard_reason = guard_result.reason
        if guard_reason:
            if ctx.error_context:
                ctx.error_context = ctx.error_context.with_stage("guard_input")
            logger.warning("[GUARD] Blocked message: %s", guard_reason)
            ctx.backlog.error(ctx.turn.session_id, ctx.turn.turn_id, 0, guard_reason)
            ctx.should_stop = True
            yield AgentEvent(
                "error",
                ErrorEventData(
                    message="Ваше сообщение заблокировано системой безопасности."
                ),
            )
            return

        logger.debug("[GUARD] Input passed: clean")
        return
