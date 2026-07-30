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
    1. Записывает cost последнего LLM response в спендинг для ВСЕХ tenant'ов
    2. Проверяет лимиты для ВСЕХ tenant'ов (не прерываясь на первом bad)
    3. В single-tenant режиме: если лимит превышен → error
    4. В composite multi-tenant режиме: логирует предупреждение, НО НЕ БЛОКИРУЕТ
       (остальные tenant'ы не должны страдать из-за одного bad)
    """

    async def process(
        self, ctx: PipelineContext, event: AgentEvent
    ) -> AgentEvent | None:
        if event.type not in ("final", "status") or not ctx.last_response:
            return event

        cost = ctx.last_response.cost
        if cost <= 0 or not ctx.turn.tenant_ids:
            return event

        # ── Phase 1: record spending for ALL tenants first ──────────────
        # (не прерываемся на первом bad — иначе good tenant'ы теряют свои записи)
        for tid in ctx.turn.tenant_ids:
            await ctx.spending.record(tid, cost)

        # ── Phase 2: check limits for ALL tenants ──────────────────────
        any_blocked = False
        for tid in ctx.turn.tenant_ids:
            allowed, reason = await ctx.spending.check_limits(tid)
            if not allowed:
                logger.warning("[SPENDING] %s", reason)
                any_blocked = True

        # ── Phase 3: block only single-tenant requests ─────────────────
        # В composite mode блокировка одного tenant'а не должна
        # лишать ответа остальных tenant'ов.
        if any_blocked and len(ctx.turn.tenant_ids) == 1:
            return AgentEvent(
                "error",
                ErrorEventData(message="Лимит расходов исчерпан для этого тенанта."),
            )

        return event


class TokenBudgetMiddleware:
    """Проверка token budget после каждого события.

    Если contributions текущего turn'а превышают лимит —
    выставляет ``ctx.should_stop = True``.

    Считает только system + turn_messages (этот turn), а не всю историю
    диалога. История уже в кеше провайдера и не растёт между вызовами.
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
        # budget = system prompt + только этот turn
        budget_msgs = [ctx.turn.messages[0]] + list(ctx.turn.turn_messages)
        token_count = estimate_tokens(budget_msgs, model=model)

        if token_count >= ctx.max_turn_tokens:
            logger.warning(
                "[TOKEN_BUDGET] Budget exceeded (%d ≥ %d) — stopping",
                token_count,
                ctx.max_turn_tokens,
            )
            ctx.should_stop = True

        return event
