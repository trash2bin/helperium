"""TDD tests: MCPClient не имеет circuit breaker для reconnect'ов.

Проблема: при падении mcp-gateway каждый tool_call делает reconnect.
Нет per-tenant счётчика неудач, нет exponential backoff, нет half-open state.

Тесты ПАДАЮТ пока circuit breaker не реализован.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from api_service.agent.mcp_client import MCPClient, _TenantConnection


@pytest.fixture
def mcp_client():
    return MCPClient()


class TestTenantConnectionFields:
    """Circuit breaker evidence must live in MCPClient._breaker_state."""

    def test_breaker_state_store_exists(self):
        """MCPClient должен иметь tenant-keyed breaker store."""
        client = MCPClient()
        assert hasattr(client, "_breaker_state"), (
            "\n\n❌ TDD FAIL: MCPClient не имеет _breaker_state словаря.\n"
            "Нужно добавить: _breaker_state: dict[str, tuple[int, float]]"
        )

    def test_connection_has_no_duplicate_breaker_fields(self):
        """_TenantConnection не должен дублировать breaker-состояние.

        Единственный источник правды — store на MCPClient; поля на коннекте
        создают два источника правды и рассинхронизацию.
        """
        conn = _TenantConnection(
            tenant_id="test",
            session=MagicMock(),
            session_ctx=MagicMock(),
        )
        assert not hasattr(conn, "consecutive_failures"), (
            "\n\n❌ TDD FAIL: _TenantConnection всё ещё имеет consecutive_failures.\n"
            "Breaker evidence должен жить только в MCPClient._breaker_state"
        )
        assert not hasattr(conn, "last_failure_time"), (
            "last_failure_time должен быть удалён с _TenantConnection"
        )


class TestCircuitBreakerReconnect:
    """Circuit breaker должен предотвращать reconnect после 3+ неудач."""

    @pytest.mark.asyncio
    async def test_reconnect_stops_after_3_failures(self, mcp_client):
        """После 3+ неудач вызов call_tool возвращает ToolResult с ошибкой.

        Вместо reconnect circuit breaker сразу возвращает
        ToolResult(ok=False, error='Circuit breaker open...').
        """
        # Создаём соединение; breaker evidence регистрируем в store клиента.
        conn = _TenantConnection(
            tenant_id="test-tenant",
            session=MagicMock(),
            session_ctx=MagicMock(),
        )
        mcp_client._store_breaker("test-tenant", 3, time.monotonic() - 5)

        mcp_client._connections["test-tenant"] = conn

        # Пробуем вызвать tool — должен вернуть ToolResult, а не reconnect
        from api_service.agent.mcp_client import _SessionProxy

        proxy = _SessionProxy(mcp_client, tenant_ids=["test-tenant"])
        result = await mcp_client.call_tool(proxy, "test_tool", {})

        assert not result.ok, (
            "\n\n❌ TDD FAIL: call_tool вернул ok=True при открытом circuit breaker.\n"
            "Должен вернуть ToolResult с ok=False"
        )
        assert "circuit" in (result.error or "").lower(), (
            "\n\n❌ TDD FAIL: ToolResult.error не содержит 'circuit'.\n"
            f"Получено: {result.error}"
        )
        assert result.tool_content is not None, "tool_content не должен быть None"

    @pytest.mark.asyncio
    async def test_breaker_resets_on_success(self, mcp_client):
        """После успешного tool_call счётчик consecutive_failures сбрасывается."""
        # Создаём реальный _TenantConnection но с моками
        real_conn = _TenantConnection(
            tenant_id="test-reset",
            session=MagicMock(),
            session_ctx=MagicMock(),
        )
        mcp_client._store_breaker("test-reset", 3, time.monotonic() - 10)

        mcp_client._connections["test-reset"] = real_conn

        # Проверяем что _mark_success очищает evidence из store
        mcp_client._mark_success(real_conn)

        assert "test-reset" not in mcp_client._breaker_state, (
            "\n\n❌ TDD FAIL: breaker evidence не очищен после _mark_success.\n"
            "После успеха store для тенанта должен быть пуст"
        )


class TestCircuitBreakerHalfOpen:
    """Circuit breaker должен поддерживать half-open state."""

    @pytest.mark.asyncio
    async def test_breaker_allows_reconnect_after_timeout(self, mcp_client):
        """После паузы (circuit timeout) _is_circuit_open возвращает False."""
        conn = _TenantConnection(
            tenant_id="test-half",
            session=MagicMock(),
            session_ctx=MagicMock(),
        )
        mcp_client._store_breaker(
            "test-half", 3, time.monotonic() - 40
        )  # > 30s timeout

        # Счётчик > MAX, но время прошло → circuit NOT open
        assert not mcp_client._is_circuit_open(conn), (
            "\n\n❌ TDD FAIL: _is_circuit_open вернул True для half-open состояния.\n"
            "После CIRCUIT_BREAKER_TIMEOUT (30s) circuit должен быть closed"
        )
