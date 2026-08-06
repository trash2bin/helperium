"""Stage 5 — Guard Output.

Проверка финального ответа на утечку system prompt или credentials.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from ..pipeline import PipelineContext
from ..types import AgentEvent

logger = logging.getLogger("api_service.agent.stages")


class GuardOutputStage:
    """Проверка финального ответа на утечку system prompt или credentials.

    Выполняется один раз — когда появляется final_content.
    Gate через _done_flags: не помечается done, пока не проверил реальный контент.
    Если ответ заблокирован — заменяет содержимое.
    """

    async def run(self, ctx: PipelineContext) -> AsyncIterator[AgentEvent]:
        if False:
            yield  # pragma: no cover — make Python treat this as async generator

        if ctx._stage_ran("guard_output"):
            return

        if not ctx.turn.final_content:
            return  # ждём пока появится контент

        # Теперь есть что проверять — маркируем done
        ctx._mark_done("guard_output")

        guard_reason = ""
        if ctx.guard_checker is not None:
            output_check = ctx.guard_checker.check_output(ctx.turn.final_content)
            if output_check.blocked:
                guard_reason = output_check.reason
        if guard_reason:
            logger.warning(
                "[GUARD] Blocked output: %s (session %s)",
                guard_reason,
                ctx.turn.session_id,
            )
            # Заменить последнее assistant сообщение
            blocked_text = "[Ответ заблокирован системой безопасности]"
            for msg in reversed(ctx.turn.messages):
                if msg.get("role") == "assistant" and msg.get("content"):
                    msg["content"] = blocked_text
                    break
            ctx.turn.final_content = blocked_text
            # Тоже правим turn_messages
            for msg in reversed(ctx.turn.turn_messages):
                if msg.get("role") == "assistant" and msg.get("content"):
                    msg["content"] = blocked_text
                    break

        return
