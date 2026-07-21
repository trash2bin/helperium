"""Pipeline — композиция Stage'ов с Middleware.

Pipeline принимает список Stage'ов (выполняются в цикле) и Middleware
(обрабатывают каждый AgentEvent). Stage'ы, которые должны выполниться
один раз, используют флаги PipelineContext._done_flags для гейтинга.

```
Pipeline.run() ─► while loop ─► for stage in stages ─► for event in stage.run(ctx)
                                  │                        │
                                  │                        └──► Middleware chain
                                  │                      SpendingMiddleware
                                  │                      BacklogMiddleware
                                  │                      TokenBudgetMiddleware
                                  └──► ctx.should_stop? ──► break
                     ─► Фаза 2 (finalization): FallbackStage → GuardOutputStage → SaveHistoryStage
```

LLMStage + ToolExecutionStage чередуются в цикле итераций, а
GuardInputStage / ToolDiscoveryStage / GuardOutputStage / FallbackStage / SaveHistoryStage
запускаются один раз (через _done_flags или как финализаторы).

Middleware (актуальный список):
- ``SpendingMiddleware`` — запись cost + проверка лимитов для tenant'ов
- ``BacklogMiddleware`` — запись событий в backlog
- ``TokenBudgetMiddleware`` — проверка лимита токенов контекста
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol

from .models import CompletionResponse
from .protocols import (
    BacklogWriter,
    ConversationStore,
    GuardChecker,
    LLMProvider,
    SpendingTracker,
)
from .turn_context import TurnContext
from .types import AgentEvent

logger = logging.getLogger("api_service.agent.pipeline")


@dataclass
class PipelineContext:
    """Контекст выполнения pipeline. Объединяет TurnContext + runtime.

    Создаётся в LLMAgent.stream_events() перед запуском pipeline.
    """

    turn: TurnContext
    llm_provider: LLMProvider
    mcp_session: (
        Any  # _SessionProxy из MCPClient (не MCPToolProvider — это proxy-объект сессии)
    )

    # Runtime-зависимости (типизированы через протоколы из protocols.py)
    store: ConversationStore
    spending: SpendingTracker
    backlog: BacklogWriter

    # Limits
    max_iterations: int = 5
    max_empty_rounds: int = 3
    max_turn_tokens: int = 8000

    # Состояние pipeline (не путать с turn)
    last_response: CompletionResponse | None = None
    should_stop: bool = False

    # Guard checker (опционально, для Stage'ов которые не хотят хардкодить синглтон)
    guard_checker: GuardChecker | None = None

    # Флаги для one-shot stage'ов (GuardInput, ToolDiscovery, SaveHistory)
    _done_flags: set[str] = field(default_factory=set)

    def _stage_ran(self, name: str) -> bool:
        return name in self._done_flags

    def _mark_done(self, name: str) -> None:
        self._done_flags.add(name)


class Stage(Protocol):
    """Этап pipeline. Принимает контекст, отдаёт события."""

    def run(self, ctx: PipelineContext) -> AsyncIterator[AgentEvent]: ...


class Middleware(Protocol):
    """Middleware. Может модифицировать или блокировать события.

    Возвращает:
      - AgentEvent — оригинальное или модифицированное событие
      - None — событие заблокировано
    """

    async def process(
        self, ctx: PipelineContext, event: AgentEvent
    ) -> AgentEvent | None: ...


class Pipeline:
    """Композиция Stage'ов в цикле с Middleware.

    Stage'ы выполняются в цикле.  Когда ``should_stop`` становится True,
    pipeline делает ещё один проход, но пропускает "активные" stage'ы
    (LLMStage, ToolExecutionStage), выполняя только "финализирующие":
    GuardOutputStage, FallbackStage, SaveHistoryStage.

    ``_finalizer_stages`` — индексы stage'ов, которые запускаются даже
    после остановки.

    Пример:
        pipeline = Pipeline(
            stages=[GuardInputStage(), ToolDiscoveryStage(),
                    LLMStage(), ToolExecutionStage(),
                    GuardOutputStage(), FallbackStage(), SaveHistoryStage()],
            middlewares=[SpendingMiddleware(), BacklogMiddleware()],
        )
        async for event in pipeline.run(ctx):
            ...
    """

    def __init__(
        self,
        stages: list[Stage],
        middlewares: list[Middleware] | None = None,
        finalizer_stages: list[Stage] | None = None,
    ) -> None:
        self._stages = stages
        self._middlewares = middlewares or []
        if finalizer_stages is not None:
            self._finalizer_stages = finalizer_stages
        else:
            # Backward compat: derive finalizers from stages by class name.
            # This allows existing test code that passes all stages in
            # ``stages`` to continue working without the new parameter.
            _finalizer_names = (
                "FallbackStage",
                "GuardOutputStage",
                "SaveHistoryStage",
            )
            self._finalizer_stages = [
                s for s in stages if type(s).__name__ in _finalizer_names
            ]

    async def run(self, ctx: PipelineContext) -> AsyncIterator[AgentEvent]:
        """Запустить pipeline.

        Фаза 1 — основной цикл: Stage'ы выполняются, пока не будет
        ``should_stop`` или одного из условий остановки.

        Фаза 2 — финализация: FallbackStage + SaveHistoryStage,
        запускаются один раз после выхода из цикла.
        """
        # ── Фаза 1: основной цикл ─────────────────────────────────────
        while not ctx.should_stop:
            for stage in self._stages:
                if ctx.should_stop:
                    break

                async for event in stage.run(ctx):
                    processed = await self._process_middleware(ctx, event)

                    if processed is not None:
                        yield processed

                        # Если финал — останавливаем pipeline
                        if processed.type == "final":
                            ctx.should_stop = True

            # ── Loop termination checks ────────────────────────────────
            if ctx.should_stop:
                break

            # Final content set → done
            if ctx.turn.final_content:
                break

            # Empty rounds limit
            if ctx.turn.empty_rounds >= ctx.max_empty_rounds:
                logger.info(
                    "[PIPELINE] Empty rounds limit hit (%d ≥ %d)",
                    ctx.turn.empty_rounds,
                    ctx.max_empty_rounds,
                )
                break

            # Max iterations
            if ctx.turn.iteration >= ctx.max_iterations - 1:
                logger.info(
                    "[PIPELINE] Max iterations hit (%d ≥ %d)",
                    ctx.turn.iteration + 1,
                    ctx.max_iterations,
                )
                break

            # Token budget check
            if ctx.max_turn_tokens > 0 and ctx.turn.messages:
                from .token_estimator import estimate_tokens

                model = getattr(ctx.llm_provider, "model", "")
                tokens = estimate_tokens(ctx.turn.messages, model=model)
                if tokens >= ctx.max_turn_tokens:
                    logger.warning(
                        "[PIPELINE] Token budget exceeded (%d ≥ %d)",
                        tokens,
                        ctx.max_turn_tokens,
                    )
                    break

            # ── Next iteration ─────────────────────────────────────────
            ctx.turn.iteration += 1
            ctx.turn.pending_calls = []

        # ── Фаза 2: финализация ──────────────────────────────────────
        # Fallback + GuardOutput + SaveHistory — один раз после цикла
        ctx.should_stop = True
        for stage in self._finalizer_stages:
            async for event in stage.run(ctx):
                processed = await self._process_middleware(ctx, event)
                if processed is not None:
                    yield processed

    async def _process_middleware(
        self, ctx: PipelineContext, event: AgentEvent
    ) -> AgentEvent | None:
        """Pass event through all middlewares. Returns None if blocked."""
        processed: AgentEvent | None = event
        for mw in self._middlewares:
            processed = await mw.process(ctx, processed)
            if processed is None:
                break
        return processed
