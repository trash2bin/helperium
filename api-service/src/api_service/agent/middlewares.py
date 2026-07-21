"""Middleware для Pipeline.

Каждый Middleware — async filter, реализующий ``Middleware`` протокол.
Обрабатывает каждый AgentEvent после выхода из Stage.

Middleware могут:
- модифицировать событие (вернуть изменённый AgentEvent)
- блокировать событие (вернуть None)
- добавить побочное действие (запись в backlog, проверка лимитов)
"""

from __future__ import annotations

import logging

from .pipeline import PipelineContext
from .token_estimator import estimate_tokens
from .types import AgentEvent, ErrorEventData

logger = logging.getLogger("api_service.agent.middlewares")


class SpendingMiddleware:
    """Запись спендинга + проверка лимитов.

    На каждый ``final`` или ``tool_calls`` event:
    1. Записывает cost последнего LLM response в спендинг для tenant'ов
    2. Проверяет лимиты — если превышен, заменяет событие на ``error``
    """

    async def process(
        self, ctx: PipelineContext, event: AgentEvent
    ) -> AgentEvent | None:
        if event.type not in ("final", "status") or not ctx.last_response:
            return event

        cost = ctx.last_response.cost
        if cost <= 0 or not ctx.turn.tenant_ids:
            return event

        for tid in ctx.turn.tenant_ids:
            # Record spending
            await ctx.spending.record(tid, cost)
            # Check limits
            allowed, reason = await ctx.spending.check_limits(tid)
            if not allowed:
                logger.warning("[SPENDING] %s", reason)
                return AgentEvent(
                    "error",
                    ErrorEventData(
                        message="Лимит расходов исчерпан для этого тенанта."
                    ),
                )

        return event


class BacklogMiddleware:
    """Запись событий в backlog (ModelBacklog).

    Пишет:
    - turn_start при первом событии любого типа
    - остальные события проходят сквозь (Stage'ы сами пишут свои вызовы)
    """

    def __init__(self) -> None:
        self._turn_started: bool = False

    async def process(
        self, ctx: PipelineContext, event: AgentEvent
    ) -> AgentEvent | None:
        if not self._turn_started:
            # Запись turn_start уже сделана в orchestrator.stream_events()
            # через backlog.turn_start() — не дублируем
            self._turn_started = True

        # Дополнительное логирование ошибок
        if event.type == "error":
            error_msg = (
                event.data.get("message", "") if isinstance(event.data, dict) else ""
            )
            logger.warning("[BACKLOG_MW] Error event: %s", error_msg)

        return event


class TokenBudgetMiddleware:
    """Проверка token budget после каждого события.

    Если суммарное количество токенов в messages превышает лимит —
    выставляет ``ctx.should_stop = True``.
    """

    async def process(
        self, ctx: PipelineContext, event: AgentEvent
    ) -> AgentEvent | None:
        if ctx.max_turn_tokens <= 0:
            return event

        # Проверяем только после добавления контента (not token stream)
        if event.type not in ("tool_result", "final", "error"):
            return event

        model = getattr(ctx.llm_provider, "model", "")
        token_count = estimate_tokens(ctx.turn.messages, model=model)

        if token_count >= ctx.max_turn_tokens:
            logger.warning(
                "[TOKEN_BUDGET] Budget exceeded (%d ≥ %d) — stopping",
                token_count,
                ctx.max_turn_tokens,
            )
            ctx.should_stop = True

        return event
