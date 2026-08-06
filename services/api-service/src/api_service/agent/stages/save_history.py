"""Stage 7 — Save History.

Сохранить turn в conversation store.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from ..pipeline import PipelineContext
from ..types import AgentEvent

logger = logging.getLogger("api_service.agent.stages")


class SaveHistoryStage:
    """Сохранить turn в conversation store.

    Выполняется один раз в конце pipeline.
    """

    async def run(self, ctx: PipelineContext) -> AsyncIterator[AgentEvent]:
        if False:
            yield  # pragma: no cover — make Python treat this as async generator

        # Сохраняем только когда turn завершён
        if not ctx.should_stop and not ctx.turn.final_content:
            return

        if ctx._stage_ran("save_history"):
            return
        ctx._mark_done("save_history")

        if not ctx.turn.turn_messages:
            return

        try:
            await ctx.store.aremember_turn(
                ctx.turn.session_id,
                ctx.turn.turn_messages,
            )
            logger.debug(
                "[SAVE_HISTORY] Saved %d messages for session %s",
                len(ctx.turn.turn_messages),
                ctx.turn.session_id,
            )
        except Exception:
            if ctx.error_context:
                ctx.error_context = ctx.error_context.with_stage("save_history")
            logger.exception(
                "[SAVE_HISTORY] Failed to save for session %s",
                ctx.turn.session_id,
            )
        return

    async def force_save(self, ctx: PipelineContext) -> None:
        """Принудительно сохранить (для аварийных ситуаций)."""
        if not ctx.turn.turn_messages:
            return

        try:
            await ctx.store.aremember_turn(
                ctx.turn.session_id,
                ctx.turn.turn_messages,
            )
        except Exception:
            if ctx.error_context:
                ctx.error_context = ctx.error_context.with_stage("save_history")
            logger.exception(
                "[SAVE_HISTORY] force_save failed for session %s",
                ctx.turn.session_id,
            )
