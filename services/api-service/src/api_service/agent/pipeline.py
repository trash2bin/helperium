"""Pipeline — композиция Stage'ов с Middleware.

Pipeline принимает список Stage'ов (выполняются в цикле) и Middleware
(обрабатывают каждый AgentEvent). Stage'ы, которые должны выполниться
один раз, используют флаги PipelineContext._done_flags для гейтинга.

```
Pipeline.run() ─► while loop ─► for stage in stages ─► for event in stage.run(ctx)
                                  │                        │
                                  │                        └──► Middleware chain
                                  │                      SpendingMiddleware
                                  │                      TokenBudgetMiddleware
                                  └──► ctx.should_stop? ──► break
                     ─► Фаза 2 (finalization): FallbackStage → GuardOutputStage → SaveHistoryStage
```

LLMStage + ToolExecutionStage чередуются в цикле итераций, а
GuardInputStage / ToolDiscoveryStage / GuardOutputStage / FallbackStage / SaveHistoryStage
запускаются один раз (через _done_flags или как финализаторы).

Middleware (актуальный список):
- ``SpendingMiddleware`` — запись cost + проверка лимитов для tenant'ов
- ``TokenBudgetMiddleware`` — проверка лимита токенов контекста
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol, Any

from .models import CompletionResponse
from .protocols import (
    BacklogWriter,
    ConversationStore,
    GuardChecker,
    LLMProvider,
    MCPSession,
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
    mcp_session: MCPSession

    # Runtime-зависимости (типизированы через протоколы из protocols.py)
    store: ConversationStore
    spending: SpendingTracker
    backlog: BacklogWriter

    # Limits — set by orchestrator from settings; plain defaults for direct construction (tests)
    max_iterations: int = int(os.environ.get("PIPELINE_MAX_ITERATIONS", "5"))
    max_empty_rounds: int = int(os.environ.get("PIPELINE_MAX_EMPTY_ROUNDS", "3"))
    max_turn_tokens: int = int(os.environ.get("PIPELINE_MAX_TURN_TOKENS", "8000"))
    max_tool_calls_per_turn: int = int(
        os.environ.get("PIPELINE_MAX_TOOL_CALLS_PER_TURN", "10")
    )

    # Structured error context (optional — pipeline works without it)
    error_context: Any | None = None

    # Tool call counter (per turn, across all iterations)
    tool_call_count: int = 0

    # True если в текущей итерации были tool_calls (не расходуем iteration)
    had_tool_calls_this_iteration: bool = False

    # Состояние pipeline (не путать с turn)
    last_response: CompletionResponse | None = None
    should_stop: bool = False

    # Guard checker (опционально, для Stage'ов которые не хотят хардкодить синглтон)
    guard_checker: GuardChecker | None = None

    # Bench metrics accumulator (populated by stages, consumed by orchestrator)
    bench: dict = field(
        default_factory=lambda: {
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "total_cost": 0.0,
            "llm_calls": 0,
            "tool_errors": 0,
            "empty_results": 0,
        }
    )

    # Флаги для one-shot stage'ов (GuardInput, ToolDiscovery, SaveHistory)
    _done_flags: set[str] = field(default_factory=set)

    def _stage_ran(self, name: str) -> bool:
        return name in self._done_flags

    def _mark_done(self, name: str) -> None:
        self._done_flags.add(name)

    def set_error_context(self, session_id: str, correlation_id: str = "") -> None:
        """Create and attach an ErrorContext for this pipeline run."""
        from .error_context import ErrorContext

        self.error_context = ErrorContext(
            session_id=session_id,
            correlation_id=correlation_id,
        )


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
            middlewares=[SpendingMiddleware()],
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

                        # Tool call limit per turn
                        if processed.type == "tool_result":
                            ctx.tool_call_count += 1
                            if ctx.tool_call_count > ctx.max_tool_calls_per_turn:
                                logger.warning(
                                    "[PIPELINE] Tool call limit reached (%d), aborting",
                                    ctx.max_tool_calls_per_turn,
                                )
                                # FallbackStage в фазе 2 финализации триммит
                                # историю и переспрашивает LLM — пользователь
                                # получает ответ, а не ошибку.
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

            # ── Next iteration ─────────────────────────────────────────
            # Не расходуем iteration когда в этом раунде были tool_calls
            # (LLM сделала полезную работу, а не просто думала).
            if not ctx.had_tool_calls_this_iteration:
                ctx.turn.iteration += 1
            ctx.had_tool_calls_this_iteration = False
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
