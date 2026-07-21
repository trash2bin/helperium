"""Tests for Pipeline Middleware.

Each middleware is tested independently.  Middleware only filters events
and mutates PipelineContext — no IO (mocked via test stores).
"""

from __future__ import annotations

import pytest

from api_service.agent.middlewares import (
    SpendingMiddleware,
    BacklogMiddleware,
    TokenBudgetMiddleware,
)
from api_service.agent.types import AgentEvent

from .helpers import (
    TestSpendingTracker,
    llm_response,
    make_pipeline_ctx,
)


class TestSpendingMiddleware:
    """SpendingMiddleware: запись cost + проверка лимитов."""

    @pytest.mark.asyncio
    async def test_records_cost_on_final(self):
        """Final event → cost записывается в spending tracker."""
        spending = TestSpendingTracker()
        ctx = await make_pipeline_ctx()
        ctx.spending = spending
        ctx.last_response = llm_response.final("Answer")
        ctx.turn.tenant_ids = ["tenant-a"]

        mw = SpendingMiddleware()
        event = AgentEvent("final", {"content": "Answer"})
        result = await mw.process(ctx, event)

        assert result is not None, "Middleware не должен блокировать событие"
        assert len(spending._records) == 1
        assert spending._records[0][0] == "tenant-a"
        assert spending._records[0][1] > 0  # cost > 0

    @pytest.mark.asyncio
    async def test_records_cost_on_tool_calls(self):
        """Tool calls event → cost записывается."""
        spending = TestSpendingTracker()
        ctx = await make_pipeline_ctx()
        ctx.spending = spending
        ctx.last_response = llm_response.tool_call("test", {})
        ctx.turn.tenant_ids = ["tenant-a"]

        mw = SpendingMiddleware()
        event = AgentEvent("status", {"phase": "tool_calls", "iteration": 0})
        result = await mw.process(ctx, event)

        assert result is not None
        assert len(spending._records) == 1

    @pytest.mark.asyncio
    async def test_blocks_on_limit_reached(self):
        """Превышен лимит → event заменяется на error."""
        spending = TestSpendingTracker()
        spending.set_blocked("tenant-a", True)
        ctx = await make_pipeline_ctx()
        ctx.spending = spending
        ctx.last_response = llm_response.final("Answer")
        ctx.turn.tenant_ids = ["tenant-a"]

        mw = SpendingMiddleware()
        event = AgentEvent("final", {"content": "Answer"})
        result = await mw.process(ctx, event)

        assert result is not None
        assert result.type == "error", f"Expected error, got {result.type}"
        assert "Лимит расходов" in str(result.data)

    @pytest.mark.asyncio
    async def test_skips_when_no_last_response(self):
        """Нет last_response — middleware пропускает."""
        ctx = await make_pipeline_ctx()
        ctx.last_response = None

        mw = SpendingMiddleware()
        event = AgentEvent("token", {"data": "hello"})
        result = await mw.process(ctx, event)
        assert result is event, "Middleware не должен трогать token-события"

    @pytest.mark.asyncio
    async def test_skips_when_cost_is_zero(self):
        """Cost=0 — ничего не записывается."""
        spending = TestSpendingTracker()
        ctx = await make_pipeline_ctx()
        ctx.spending = spending
        resp = llm_response.reasoning("thinking")  # cost=0
        ctx.last_response = resp
        ctx.turn.tenant_ids = ["tenant-a"]

        mw = SpendingMiddleware()
        event = AgentEvent("final", {"content": "Answer"})
        result = await mw.process(ctx, event)
        assert result is not None
        assert len(spending._records) == 0


class TestBacklogMiddleware:
    """BacklogMiddleware: логирование событий."""

    @pytest.mark.asyncio
    async def test_events_pass_through(self):
        """Все события проходят через middleware без изменений."""
        ctx = await make_pipeline_ctx()
        mw = BacklogMiddleware()
        events = [
            AgentEvent("token", {"data": "hello"}),
            AgentEvent("final", {"content": "world"}),
            AgentEvent("tool_call", {"name": "test", "arguments": {}}),
            AgentEvent("error", {"message": "test error"}),
        ]
        for ev in events:
            result = await mw.process(ctx, ev)
            assert result is ev, f"Middleware изменил событие {ev.type}: {result}"
            assert result.type == ev.type


class TestTokenBudgetMiddleware:
    """TokenBudgetMiddleware: проверка лимита токенов."""

    @pytest.mark.asyncio
    async def test_passes_within_budget(self):
        """В рамках лимита — событие проходит."""
        ctx = await make_pipeline_ctx(max_turn_tokens=10000)
        mw = TokenBudgetMiddleware()

        event = AgentEvent("tool_result", {"result": "small"})
        result = await mw.process(ctx, event)
        assert result is not None

    @pytest.mark.asyncio
    async def test_skips_for_token_stream(self):
        """token events не проверяются (проверка только после tool_result/final)."""
        ctx = await make_pipeline_ctx(max_turn_tokens=10)  # очень маленький лимит
        mw = TokenBudgetMiddleware()

        event = AgentEvent("token", {"data": "hello"})
        result = await mw.process(ctx, event)
        assert result is not None, "token events не должны блокироваться"
