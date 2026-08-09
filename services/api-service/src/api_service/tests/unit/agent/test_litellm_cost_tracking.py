"""TDD test: LiteLLMProvider.cost всегда 0.0 — spending tracking сломан.

Проблема: `services/api-service/src/api_service/agent/litellm_provider.py`
метод complete() возвращает ``cost=0.0`` хардкодом. При этом
litellm.ModelResponse.usage.cost содержит реальную стоимость от API.

SpendingMiddleware.process() проверяет ``if cost <= 0: return event`` —
пропускает все события. Фактически spending tracking полностью мёртв.

Тест ПАДАЕТ пока LiteLLMProvider.complete() не начнёт извлекать cost
из ответа лителлма.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from litellm.types.utils import ModelResponse, Choices, Message, Usage

from api_service.agent.litellm_provider import LiteLLMProvider
from api_service.agent.models import CompletionRequest, CompletionResponse, UsageInfo
from api_service.agent.middlewares import SpendingMiddleware
from api_service.agent.pipeline import PipelineContext
from api_service.agent.types import AgentEvent


class TestLiteLLMCostTracking:
    """LiteLLMProvider должен возвращать cost > 0 когда LLM вернула usage."""

    @pytest.mark.asyncio
    async def test_cost_extracted_from_litellm_response(self):
        """После complete() response.cost должен быть > 0.

        Сейчас (баг): hardcoded cost=0.0 — всегда 0.
        Ожидание: cost берётся из litellm.ModelResponse.usage.cost.
        """
        provider = LiteLLMProvider(
            model="openai/gpt-4o-mini",
            api_key="test-key",
        )

        # Строим настоящий ModelResponse с usage.cost
        msg = Message(
            content="Hello, world!",
            role="assistant",
        )
        choice = Choices(finish_reason="stop", index=0, message=msg)
        usage = Usage(prompt_tokens=50, completion_tokens=100, total_tokens=150)
        usage.cost = 0.00235

        mock_response = ModelResponse(
            id="chatcmpl-test",
            created=1234567890,
            model="gpt-4o-mini",
            object="chat.completion",
            choices=[choice],
        )
        mock_response.usage = usage

        req = CompletionRequest(
            messages=[{"role": "user", "content": "hello"}],
            stream=False,
        )

        with patch(
            "api_service.agent.litellm_provider.litellm.acompletion",
            new=AsyncMock(return_value=mock_response),
        ):
            response = await provider.complete(req)

        # ⚡ TDD-контракт: cost должен быть > 0
        assert response.cost > 0, (
            f"\n\n❌ TDD FAIL: cost={response.cost}, ожидалось > 0.\n"
            "LiteLLM вернул usage.cost=0.00235, но complete() вернул 0.0.\n"
            "Фикс: извлечь cost из response.usage.cost вместо хардкода.\n"
            "Текущий код: cost=0.0 (всегда)"
        )

        # Дополнительно: cost должен совпадать с usage.cost от litellm
        assert response.cost == pytest.approx(0.00235, rel=0.01), (
            f"cost={response.cost} не совпадает с usage.cost=0.00235"
        )

    @pytest.mark.asyncio
    async def test_cost_is_zero_when_litellm_cost_is_none(self):
        """Когда litellm.usage.cost = None — cost должен быть 0.0.

        Это может случиться при локальных моделях (Ollama), которые
        не возвращают cost в usage.
        """
        provider = LiteLLMProvider(
            model="ollama_chat/qwen2.5:0.5b",
        )

        msg = Message(content="Hello", role="assistant")
        choice = Choices(finish_reason="stop", index=0, message=msg)
        usage = Usage(prompt_tokens=50, completion_tokens=100, total_tokens=150)
        usage.cost = None  # Локальная модель — cost нет

        mock_response = ModelResponse(
            id="chatcmpl-test",
            created=1234567890,
            model="ollama/qwen2.5:0.5b",
            object="chat.completion",
            choices=[choice],
        )
        mock_response.usage = usage

        req = CompletionRequest(
            messages=[{"role": "user", "content": "hello"}],
            stream=False,
        )

        with patch(
            "api_service.agent.litellm_provider.litellm.acompletion",
            new=AsyncMock(return_value=mock_response),
        ):
            response = await provider.complete(req)

        # Когда usage.cost=None — cost должен быть 0.0 (безопасный fallback)
        assert response.cost == 0.0, (
            f"cost={response.cost} должен быть 0.0 когда usage.cost=None"
        )

    @pytest.mark.asyncio
    async def test_cost_is_zero_when_usage_is_none(self):
        """Когда litellm вообще не вернул usage — cost должен быть 0.0 (не падать)."""
        provider = LiteLLMProvider(
            model="openai/gpt-4o-mini",
            api_key="test-key",
        )

        msg = Message(content="Hello", role="assistant")
        choice = Choices(finish_reason="stop", index=0, message=msg)

        mock_response = ModelResponse(
            id="chatcmpl-test",
            created=1234567890,
            model="gpt-4o-mini",
            object="chat.completion",
            choices=[choice],
        )
        mock_response.usage = None  # Вообще нет usage

        req = CompletionRequest(
            messages=[{"role": "user", "content": "hello"}],
            stream=False,
        )

        with patch(
            "api_service.agent.litellm_provider.litellm.acompletion",
            new=AsyncMock(return_value=mock_response),
        ):
            response = await provider.complete(req)

        # Когда usage нет — cost должен быть 0 (безопасный fallback)
        assert response.cost == 0.0, (
            f"cost={response.cost} должен быть 0.0 когда usage=None"
        )


class TestSpendingMiddlewareCostFlow:
    """SpendingMiddleware должен получать cost > 0 от last_response."""

    @pytest.mark.asyncio
    async def test_middleware_processes_positive_cost(self):
        """SpendingMiddleware.process() должен обрабатывать cost > 0.

        Сейчас (баг): middleware проверяет ``if cost <= 0: return event`` —
        и всегда возвращает event без записи spending, потому что
        LiteLLMProvider всегда возвращает cost=0.0.
        """
        from unittest.mock import AsyncMock

        turn = AsyncMock()
        turn.tenant_ids = ["tenant-1"]
        turn.messages = []
        turn.turn_messages = []
        turn.final_content = "test"
        turn.session_id = "test"
        turn.turn_id = "test"
        turn.iteration = 0
        turn.empty_rounds = 0
        turn.tools = []
        turn.pending_calls = []
        turn.tool_results = []

        llm = AsyncMock()
        llm.model = "test"
        llm.api_base = None
        llm.enable_thinking = False

        mcp = AsyncMock()
        store = AsyncMock()

        spending = AsyncMock()
        spending.record.return_value = None
        spending.check_limits.return_value = (True, "")

        backlog = AsyncMock()

        ctx = PipelineContext(
            turn=turn,
            llm_provider=llm,
            mcp_session=mcp,
            store=store,
            spending=spending,
            backlog=backlog,
        )

        # last_response с cost > 0 (реалистичным)
        ctx.last_response = CompletionResponse(
            content="test",
            usage=UsageInfo(prompt_tokens=50, completion_tokens=100, total_tokens=150),
            cost=0.00235,
            content_tokens=["test"],
        )

        event = AgentEvent(type="final", data={"content": "test"})

        middleware = SpendingMiddleware()
        result = await middleware.process(ctx, event)

        # Middleware ДОЛЖЕН записать spending (cost > 0, tenant_ids есть)
        assert spending.record.called, (
            "\n\n❌ TDD FAIL: SpendingMiddleware не вызвал spending.record().\n"
            "Причина: cost=0.0 от LiteLLMProvider → middleware думает что cost <= 0.\n"
            "Фикс: LiteLLMProvider.complete() должен возвращать реальный cost."
        )

        # Проверяем что record был вызван с правильным tenant_id и cost
        call_args = spending.record.call_args
        assert call_args is not None
        recorded_tenant, recorded_cost = call_args[0]
        assert recorded_tenant == "tenant-1"
        assert recorded_cost == pytest.approx(0.00235, rel=0.01)

        # Middleware не должен блокировать событие (лимит не превышен)
        assert result is not None, "Middleware не должен блокировать событие"
        assert result.type == "final", (
            "Middleware должен пропустить событие (лимит в порядке)"
        )
