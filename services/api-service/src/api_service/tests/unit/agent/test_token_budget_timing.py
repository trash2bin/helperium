"""TDD test: TokenBudgetMiddleware проверяет ПОСЛЕ LLM call, а не ДО.

Проблема: TokenBudgetMiddleware.process() проверяет бюджет ТОЛЬКО
на событиях tool_result/final/error — уже ПОСЛЕ того как LLM была
вызвана и токены потрачены.

Текущий flow:
  Pipeline.run() → LLMStage.run() → _call_llm() [тратим токены]
                                      → yield events → MIDDLEWARE [проверка ПОСЛЕ]

Правильный flow:
  Pipeline.run() → [проверить budget ДО] → LLMStage.run() → _call_llm()

Тест доказывает что LLMStage вызывает _call_llm() не проверив budget.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from api_service.agent.pipeline import PipelineContext
from api_service.agent.stages import LLMStage
from api_service.agent.turn_context import TurnContext


class TestTokenBudgetBeforeLLM:
    """LLMStage должен проверять budget ДО вызова complete()."""

    @pytest.mark.asyncio
    async def test_llm_called_despite_exceeded_budget(self):
        """ДОКАЗЫВАЕМ: complete() вызывается когда budget уже превышен.

        Создаём TurnContext с сообщениями > 500 токенов и max_turn_tokens=50.
        Запускаем LLMStage.run(). Проверяем что complete() БЫЛ ВЫЗВАН.

        TDD: этот тест ПАДАЕТ ТОЛЬКО когда фикс внедрён (complete() НЕ вызывается).
        До фикса тест ПРОХОДИТ (подтверждая баг).
        """
        # ── 1. TurnContext с длинными сообщениями (>500 токенов) ────────
        long_text = "проверка " * 2000  # ~2000 слов, >> 500 токенов
        turn = TurnContext(
            session_id="test",
            messages=[
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": long_text},
            ],
            turn_messages=[{"role": "user", "content": long_text}],
            tools=[],
        )

        # ── 2. LLM spy — падает если вызван ─────────────────────────────
        llm = AsyncMock()
        llm.model = "test-model"
        llm.api_base = "http://test"
        llm.enable_thinking = False

        # Симулируем ответ LLM без tool_calls, без reasoning
        response = AsyncMock(spec=["content", "tool_calls", "reasoning_content"])
        response.content = ""
        response.content_tokens = []
        response.tool_calls = []
        response.reasoning_content = None
        response.usage = None
        response.cost = 0.0
        llm.complete = AsyncMock(return_value=response)

        mcp = AsyncMock()
        backlog = AsyncMock()

        # ── 3. PipelineContext с низким budget ──────────────────────────
        ctx = PipelineContext(
            turn=turn,
            llm_provider=llm,
            mcp_session=mcp,
            store=AsyncMock(),
            spending=AsyncMock(),
            backlog=backlog,
            max_turn_tokens=50,  # крайне низкий — сообщения явно превышают
        )

        # ── 4. Запускаем LLMStage ───────────────────────────────────────
        stage = LLMStage()
        events = []
        async for event in stage.run(ctx):
            events.append(event)

        # ⚡ TDD-контракт: complete() НЕ должен быть вызван
        # Сейчас (баг): complete() вызывается не глядя на budget
        # После фикса: LLMStage проверяет budget и не вызывает complete()
        assert not llm.complete.called, (
            "\n\n❌ TDD FAIL: llm_provider.complete() БЫЛ ВЫЗВАН несмотря на то,\n"
            "что messages уже превышали max_turn_tokens=50.\n"
            "LLMStage не проверяет budget — токены тратятся впустую.\n\n"
            "Фикс: В LLMStage.run() нужно проверять БЮДЖЕТ ДО вызова _call_llm():\n\n"
            "  async def run(self, ctx: PipelineContext):\n"
            "      if ctx.should_stop:\n"
            "          return\n"
            "      # ✋ NEW: проверка budget ДО вызова LLM\n"
            "      if _budget_exceeded(ctx):\n"
            "          ctx.should_stop = True\n"
            "          return\n"
            "      response = await self._call_llm(ctx)\n"
            "      ..."
        )

    @pytest.mark.asyncio
    async def test_llm_called_when_budget_ok(self):
        """Happy path: когда budget НЕ превышен — complete() вызывается."""
        turn = TurnContext(
            session_id="test",
            messages=[
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": "short hello"},
            ],
            turn_messages=[{"role": "user", "content": "short hello"}],
            tools=[],
        )

        llm = AsyncMock()
        llm.model = "test-model"
        llm.api_base = "http://test"
        llm.enable_thinking = False

        resp = AsyncMock(spec=["content", "tool_calls", "reasoning_content"])
        resp.content = "Hello!"
        resp.content_tokens = ["Hello!"]
        resp.tool_calls = []
        resp.reasoning_content = None
        resp.usage = None
        resp.cost = 0.0
        llm.complete = AsyncMock(return_value=resp)

        ctx = PipelineContext(
            turn=turn,
            llm_provider=llm,
            mcp_session=AsyncMock(),
            store=AsyncMock(),
            spending=AsyncMock(),
            backlog=AsyncMock(),
            max_turn_tokens=5000,
        )

        stage = LLMStage()
        events = []
        async for event in stage.run(ctx):
            events.append(event)

        assert llm.complete.called, "complete() должен быть вызван когда budget OK"
