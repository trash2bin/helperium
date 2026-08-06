"""TDD test: MEDIUM-7 — Composite multi-tenant: tenant-A с превышенным
бюджетом блокирует tenant-B.

Проблема: SpendingMiddleware.record() и check_limits() в цикле per tenant.
Если tenant-A превысил лимит, ВЕСЬ запрос блокируется — tenant-B получает error,
хотя его бюджет в порядке. Tenant-B может специально добавить tenant-A в
composite X-Tenant-ID заголовок чтобы DOS-ить его запросы.

Текущий баг в middlewares.py:
```
for tid in ctx.turn.tenant_ids:
    await ctx.spending.record(tid, cost)
    allowed, reason = await ctx.spending.check_limits(tid)
    if not allowed:
        return AgentEvent("error", ...)    # ← блокирует ВСЕХ tenant'ов
return event
```

Правильное поведение:
- record() должен вызываться для ВСЕХ tenant'ов (не прерываться на первом bad)
- error event должен содержать информацию о КОНКРЕТНОМ tenant'е, а не generic
- tenant-B не должен страдать из-за tenant-A
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from api_service.agent.middlewares import SpendingMiddleware
from api_service.agent.pipeline import PipelineContext
from api_service.agent.types import AgentEvent, FinalEventData


class MockSpendingTracker:
    """Mock SpendingTracker с разными лимитами для разных tenant'ов."""

    def __init__(self):
        self._records: dict[str, float] = {}
        self._budgets: dict[str, float] = {}

    def set_budget(self, tenant_id: str, budget: float) -> None:
        self._budgets[tenant_id] = budget

    def set_spending(self, tenant_id: str, spent: float) -> None:
        self._records[tenant_id] = spent

    async def record(self, tenant_id: str, cost: float) -> None:
        current = self._records.get(tenant_id, 0.0)
        self._records[tenant_id] = current + cost

    async def check_limits(self, tenant_id: str) -> tuple[bool, str]:
        budget = self._budgets.get(tenant_id, 50.0)
        if budget <= 0:
            return True, ""
        spent = self._records.get(tenant_id, 0.0)
        if spent >= budget:
            return False, f"Tenant {tenant_id} exceeded budget ${budget}"
        return True, ""


def _make_ctx(tenant_ids: list[str], spending_tracker) -> PipelineContext:
    """Create minimal PipelineContext for SpendingMiddleware testing."""
    turn = MagicMock()
    turn.tenant_ids = tenant_ids
    turn.session_id = "test-session"
    turn.turn_id = "test-turn"
    turn.iteration = 0
    turn.messages = []
    turn.turn_messages = []
    turn.tools = []
    turn.pending_calls = []
    turn.tool_results = []
    turn.final_content = ""
    turn.empty_rounds = 0

    llm = MagicMock()
    llm.model = "test-model"
    llm.api_base = "http://test"
    llm.enable_thinking = False

    mcp = AsyncMock()
    store = AsyncMock()
    backlog = AsyncMock()

    ctx = PipelineContext(
        turn=turn,
        llm_provider=llm,
        mcp_session=mcp,
        store=store,
        spending=spending_tracker,
        backlog=backlog,
    )
    return ctx


class TestSpendingCompositeTenantIsolation:
    """SpendingMiddleware должен изолировать tenant'ов в composite режиме."""

    # ── Single tenant (happy path) ──────────────────────────────────────

    @pytest.mark.asyncio
    async def test_single_good_tenant_passes(self):
        """Один tenant с нормальным бюджетом → событие проходит."""
        tracker = MockSpendingTracker()
        tracker.set_budget("good-t1", 100.0)
        tracker.set_spending("good-t1", 5.0)  # $5 spent, $95 remaining

        ctx = _make_ctx(["good-t1"], tracker)
        ctx.last_response = MagicMock()
        ctx.last_response.cost = 2.0

        mw = SpendingMiddleware()
        event = AgentEvent("final", FinalEventData(content="hello"))

        result = await mw.process(ctx, event)

        assert result is not None, "Good tenant: событие не должно быть заблокировано"
        assert result.type != "error", (
            f"Good tenant: событие не должно стать error. "
            f"Получено: {result.type} = {result.data}"
        )
        # Spending должен быть записан
        assert tracker._records.get("good-t1", 0.0) >= 7.0, (
            f"Spending не записан: ожидалось >= 7.0, получено {tracker._records.get('good-t1')}"
        )

    @pytest.mark.asyncio
    async def test_single_bad_tenant_blocked(self):
        """Один tenant с превышенным бюджетом → error."""
        tracker = MockSpendingTracker()
        tracker.set_budget("bad-t1", 10.0)
        tracker.set_spending("bad-t1", 50.0)  # $50 spent >> $10 budget

        ctx = _make_ctx(["bad-t1"], tracker)
        ctx.last_response = MagicMock()
        ctx.last_response.cost = 2.0

        mw = SpendingMiddleware()
        event = AgentEvent("final", FinalEventData(content="hello"))

        result = await mw.process(ctx, event)

        assert result is not None, "Должен быть error event (не None)"
        assert result.type == "error", (
            f"Bad tenant: должно быть error событие. Получено: {result.type}"
        )

    # ── Composite multi-tenant (проблема MEDIUM-7) ─────────────────────

    @pytest.mark.asyncio
    async def test_good_tenant_not_blocked_by_bad_tenant(self):
        """Composite: good-tenant НЕ должен блокироваться из-за bad-tenant.

        Сейчас (баг): SpendingMiddleware.process() возвращает error на первом
        tenant'е с превышенным бюджетом. Если tenant_ids = ['good', 'bad'],
        то:
        - если good первый → его spending запишется, но bad вызовет error
        - если bad первый → error сразу, good даже не записывается

        В обоих случаях good-tenant получает error и не видит ответа.
        """
        tracker = MockSpendingTracker()
        tracker.set_budget("good-tenant", 100.0)
        tracker.set_budget("bad-tenant", 10.0)
        tracker.set_spending("good-tenant", 5.0)  # $5 spent, ok
        tracker.set_spending("bad-tenant", 50.0)  # $50 >> $10, превышен

        ctx = _make_ctx(["good-tenant", "bad-tenant"], tracker)
        ctx.last_response = MagicMock()
        ctx.last_response.cost = 2.0

        mw = SpendingMiddleware()
        event = AgentEvent("final", FinalEventData(content="important data"))

        result = await mw.process(ctx, event)

        # ⚡ TDD: good-tenant не должен получать error
        assert result is not None, (
            "\n\n❌ TDD FAIL: хороший tenant получил блокировку из-за плохого.\n"
            "SpendingMiddleware.process() должен пропускать good-tenant "
            "даже если bad-tenant превысил бюджет.\n"
            "Сейчас (баг): цикл for по tenant_ids прерывается на первом bad "
            "и возвращает error — блокируя ВСЕХ."
        )

        if result.type == "error":
            pytest.fail(
                "\n\n❌ TDD FAIL: good-tenant получил error из-за bad-tenant.\n"
                f"Событие: {result.data}\n"
                "Фикс: SpendingMiddleware должен:\n"
                "  1. Записать spending для ВСЕХ tenant'ов (не прерываться)\n"
                "  2. Вернуть оригинальное событие, а не error\n"
                "  3. Логировать предупреждение для bad-tenant, но не блокировать"
            )

    @pytest.mark.asyncio
    async def test_spending_recorded_for_all_tenants(self):
        """В composite режиме spending должен записываться для ВСЕХ tenant'ов.

        Сейчас (баг): if bad tenant is first in list → cycle breaks early,
        good tenant's spending is NEVER recorded.
        """
        tracker = MockSpendingTracker()
        tracker.set_budget("bad-tenant", 10.0)
        tracker.set_budget("good-tenant", 100.0)
        tracker.set_spending("bad-tenant", 50.0)  # превышен
        tracker.set_spending("good-tenant", 5.0)  # норма

        # bad-tenant ПЕРВЫЙ в списке (чтобы поймать баг)
        ctx = _make_ctx(["bad-tenant", "good-tenant"], tracker)
        ctx.last_response = MagicMock()
        ctx.last_response.cost = 2.0

        mw = SpendingMiddleware()
        event = AgentEvent("final", FinalEventData(content="test"))

        _ = await mw.process(ctx, event)

        # ⚡ TDD: spending должен быть записан для good-tenant
        good_spent = tracker._records.get("good-tenant", 0.0)
        assert good_spent >= 7.0, (
            f"\n\n❌ TDD FAIL: spending good-tenant не записан.\n"
            f"Текущее: ${good_spent}, ожидалось >= $7.0\n"
            f"bad-tenant первый в списке → цикл прервался до good-tenant.\n"
            f"Фикс: записывать spending для ВСЕХ tenant'ов ДО проверки лимитов."
        )

        bad_spent = tracker._records.get("bad-tenant", 0.0)
        assert bad_spent >= 52.0, f"bad-tenant spending не обновился: ${bad_spent}"

    @pytest.mark.asyncio
    async def test_all_good_tenants_pass_in_composite(self):
        """Все tenant'ы с нормальным бюджетом → событие проходит."""
        tracker = MockSpendingTracker()
        tracker.set_budget("t1", 100.0)
        tracker.set_budget("t2", 100.0)
        tracker.set_budget("t3", 100.0)
        tracker.set_spending("t1", 5.0)
        tracker.set_spending("t2", 10.0)
        tracker.set_spending("t3", 15.0)

        ctx = _make_ctx(["t1", "t2", "t3"], tracker)
        ctx.last_response = MagicMock()
        ctx.last_response.cost = 1.0

        mw = SpendingMiddleware()
        event = AgentEvent("final", FinalEventData(content="ok"))

        result = await mw.process(ctx, event)

        assert result is not None, "Все tenant'ы в норме — событие не блокируется"
        assert result.type != "error", (
            f"Все tenant'ы в норме, но получен error: {result.data}"
        )
