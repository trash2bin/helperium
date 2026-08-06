"""Stage 6 — Fallback.

Fallback — если после всех итераций pipeline не дал финала.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from ..models import CompletionRequest
from ..pipeline import PipelineContext
from ..prompts import FALLBACK_GENERIC
from ..token_estimator import trim_for_fallback
from ..types import AgentEvent, FinalEventData

logger = logging.getLogger("api_service.agent.stages")


class FallbackStage:
    """Fallback — если после всех итераций pipeline не дал финала.

    Выполняется один раз, только когда ``ctx.should_stop == True`` и
    ``ctx.turn.final_content`` ещё пуст. Триммит историю до
    system + последние 2 exchange, вызывает LLM, стримит ответ.

    Использует llm_provider.complete() и итерирует content_tokens как стрим.
    """

    async def run(self, ctx: PipelineContext) -> AsyncIterator[AgentEvent]:
        # Fallback — только когда pipeline остановлен
        if not ctx.should_stop:
            return

        if ctx._stage_ran("fallback"):
            return
        ctx._mark_done("fallback")

        # Если финал уже есть — не нужен fallback
        if ctx.turn.final_content:
            return

        fallback_messages = trim_for_fallback(ctx.turn.messages)
        logger.info(
            "[FALLBACK] Trimming %d messages to %d for fallback",
            len(ctx.turn.messages),
            len(fallback_messages),
        )

        req = CompletionRequest(
            messages=fallback_messages,
            stream=True,
            tenant_ids=ctx.turn.tenant_ids,
        )

        try:
            response = await ctx.llm_provider.complete(req)
        except Exception:
            if ctx.error_context:
                ctx.error_context = ctx.error_context.with_stage("fallback")
            logger.exception("[FALLBACK] LLM call failed")
            # Генерический ответ
            yield AgentEvent("token", {"data": FALLBACK_GENERIC})
            ctx.turn.final_content = FALLBACK_GENERIC
            ctx.turn.turn_messages.append(
                {"role": "assistant", "content": FALLBACK_GENERIC}
            )
            yield AgentEvent("final", FinalEventData(content=FALLBACK_GENERIC))
            return

        fallback_parts: list[str] = []
        for token in response.content_tokens:
            fallback_parts.append(token)
            yield AgentEvent("token", {"data": token})

        full_answer = "".join(fallback_parts) if fallback_parts else FALLBACK_GENERIC

        if not fallback_parts:
            yield AgentEvent("token", {"data": FALLBACK_GENERIC})

        ctx.turn.final_content = full_answer
        ctx.turn.turn_messages.append({"role": "assistant", "content": full_answer})

        yield AgentEvent("final", FinalEventData(content=full_answer))
        ctx.should_stop = True
