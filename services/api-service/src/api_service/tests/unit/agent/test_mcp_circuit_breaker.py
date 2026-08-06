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
    """_TenantConnection должен иметь поля для circuit breaker."""

    def test_consecutive_failures_field_exists(self):
        """_TenantConnection должен иметь consecutive_failures счётчик."""
        conn = _TenantConnection(
            tenant_id="test",
            session=MagicMock(),
            http_ctx=MagicMock(),
            session_ctx=MagicMock(),
        )
        assert hasattr(conn, "consecutive_failures"), (
            "\n\n❌ TDD FAIL: _TenantConnection не имеет consecutive_failures поля.\n"
            "Нужно добавить: consecutive_failures: int = 0"
        )

    def test_last_failure_time_field_exists(self):
        """_TenantConnection должен иметь last_failure_time."""
        conn = _TenantConnection(
            tenant_id="test",
            session=MagicMock(),
            http_ctx=MagicMock(),
            session_ctx=MagicMock(),
        )
        assert hasattr(conn, "last_failure_time"), (
            "\n\n❌ TDD FAIL: _TenantConnection не имеет last_failure_time поля.\n"
            "Нужно добавить: last_failure_time: float = 0.0"
        )


class TestCircuitBreakerReconnect:
    """Circuit breaker должен предотвращать reconnect после 3+ неудач."""

    @pytest.mark.asyncio
    async def test_reconnect_stops_after_3_failures(self, mcp_client):
        """После 3+ неудач вызов call_tool возвращает ToolResult с ошибкой.

        Вместо reconnect circuit breaker сразу возвращает
        ToolResult(ok=False, error='Circuit breaker open...').
        """
        # Создаём соединение в состоянии open circuit (3 failures, недавно)
        conn = _TenantConnection(
            tenant_id="test-tenant",
            session=MagicMock(),
            http_ctx=MagicMock(),
            session_ctx=MagicMock(),
        )
        conn.consecutive_failures = 3
        conn.last_failure_time = time.monotonic() - 5  # 5s ago, < 30s timeout

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
            http_ctx=MagicMock(),
            session_ctx=MagicMock(),
        )
        real_conn.consecutive_failures = 3
        real_conn.last_failure_time = time.monotonic() - 10

        mcp_client._connections["test-reset"] = real_conn

        # Проверяем что _mark_success сбрасывает счётчик
        mcp_client._mark_success(real_conn)

        assert real_conn.consecutive_failures == 0, (
            "\n\n❌ TDD FAIL: consecutive_failures не сброшен после _mark_success.\n"
            "После успеха счётчик должен стать 0"
        )
        assert real_conn.last_failure_time == 0.0, (
            "last_failure_time должен быть 0 после _mark_success"
        )


class TestCircuitBreakerHalfOpen:
    """Circuit breaker должен поддерживать half-open state."""

    @pytest.mark.asyncio
    async def test_breaker_allows_reconnect_after_timeout(self, mcp_client):
        """После паузы (circuit timeout) _is_circuit_open возвращает False."""
        conn = _TenantConnection(
            tenant_id="test-half",
            session=MagicMock(),
            http_ctx=MagicMock(),
            session_ctx=MagicMock(),
        )
        conn.consecutive_failures = 3
        conn.last_failure_time = time.monotonic() - 40  # > 30s timeout

        # Счётчик > MAX, но время прошло → circuit NOT open
        assert not mcp_client._is_circuit_open(conn), (
            "\n\n❌ TDD FAIL: _is_circuit_open вернул True для half-open состояния.\n"
            "После CIRCUIT_BREAKER_TIMEOUT (30s) circuit должен быть closed"
        )
