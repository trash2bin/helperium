"""TDD tests: MCPClient не имеет TTL garbage collection для сессий.

Проблема: _connections dict растёт без лимита. Нет фонового cleaner'а,
который закрывает idle сессии.

Тесты ПАДАЮТ пока GC не реализован.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest

from api_service.agent.mcp_client import MCPClient, _TenantConnection


@pytest.fixture
def mcp_client():
    client = MCPClient()
    yield client
    # Cleanup
    for conn in list(client._connections.values()):
        if hasattr(conn, "close") and not isinstance(conn.close, PropertyMock):
            pass


class TestCleanupMethodExists:
    """MCPClient должен иметь методы для garbage collection."""

    def test_cleanup_stale_connections_method_exists(self, mcp_client):
        """MCPClient должен иметь _cleanup_stale_connections() метод.

        Проверки:
        - idle > max_idle = закрыть и удалить
        - active = не трогать
        - ошибка close() = не падать
        """
        assert hasattr(mcp_client, "_cleanup_stale_connections"), (
            "\n\n❌ TDD FAIL: MCPClient не имеет _cleanup_stale_connections() метода.\n"
            "Нужно добавить async def _cleanup_stale_connections(self, max_idle_seconds=600)"
        )
        assert asyncio.iscoroutinefunction(mcp_client._cleanup_stale_connections), (
            "_cleanup_stale_connections должна быть async def"
        )

    def test_gc_task_methods_exist(self, mcp_client):
        """MCPClient должен иметь start_gc() и stop_gc() методы."""
        assert hasattr(mcp_client, "start_gc"), (
            "\n\n❌ TDD FAIL: MCPClient не имеет start_gc() метода.\n"
            "Нужно добавить async def start_gc(self, interval_seconds=60)"
        )
        assert hasattr(mcp_client, "stop_gc"), (
            "\n\n❌ TDD FAIL: MCPClient не имеет stop_gc() метода."
        )


class TestCleanupIdleConnections:
    """_cleanup_stale_connections должен закрывать idle сессии."""

    @pytest.mark.asyncio
    async def test_cleans_idle_connections(self, mcp_client):
        """Сессии idle > 10 минут должны быть закрыты и удалены."""
        old_conn = MagicMock(spec=_TenantConnection)
        old_conn.tenant_id = "old-session"
        old_conn.close = AsyncMock()
        old_conn.last_used = time.monotonic() - 700  # ~12 min idle

        mcp_client._connections["old-session"] = old_conn

        await mcp_client._cleanup_stale_connections(max_idle_seconds=600)

        assert "old-session" not in mcp_client._connections, (
            "\n\n❌ TDD FAIL: Idle сессия не удалена после _cleanup_stale_connections.\n"
            "Сессия была idle 700s при max_idle=600s"
        )
        old_conn.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_preserves_active_connections(self, mcp_client):
        """Активные сессии (использованные недавно) НЕ должны быть закрыты."""
        active_conn = MagicMock(spec=_TenantConnection)
        active_conn.tenant_id = "active-session"
        active_conn.close = AsyncMock()
        active_conn.last_used = time.monotonic() - 10  # 10 seconds ago

        mcp_client._connections["active-session"] = active_conn

        await mcp_client._cleanup_stale_connections(max_idle_seconds=600)

        assert "active-session" in mcp_client._connections, (
            "\n\n❌ TDD FAIL: Активная сессия была удалена.\n"
            "Сессия использовалась 10s назад при max_idle=600s"
        )
        active_conn.close.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handles_close_errors_gracefully(self, mcp_client):
        """Ошибки close() не должны прерывать очистку других сессий."""
        broken_conn = MagicMock(spec=_TenantConnection)
        broken_conn.tenant_id = "broken"
        broken_conn.close = AsyncMock(side_effect=RuntimeError("close failed"))
        broken_conn.last_used = time.monotonic() - 700

        ok_conn = MagicMock(spec=_TenantConnection)
        ok_conn.tenant_id = "ok"
        ok_conn.close = AsyncMock()
        ok_conn.last_used = time.monotonic() - 700

        mcp_client._connections["broken"] = broken_conn
        mcp_client._connections["ok"] = ok_conn

        await mcp_client._cleanup_stale_connections(max_idle_seconds=600)

        assert "broken" not in mcp_client._connections, (
            "broken сессия должна быть удалена даже если close упал"
        )
        assert "ok" not in mcp_client._connections, (
            "ok сессия должна быть удалена (ошибка broken не должна мешать)"
        )


class TestGCBackgroundTask:
    """MCPClient должен запускать фоновый GC task."""

    @pytest.mark.asyncio
    async def test_start_gc_creates_background_task(self, mcp_client):
        """start_gc() должен запускать asyncio.Task."""
        await mcp_client.start_gc(interval_seconds=1)

        assert hasattr(mcp_client, "_gc_task"), (
            "\n\n❌ TDD FAIL: start_gc() не создал _gc_task"
        )
        assert mcp_client._gc_task is not None, "_gc_task не должен быть None"
        assert not mcp_client._gc_task.done(), "GC task должен быть запущен"

        # Cleanup
        if hasattr(mcp_client, "stop_gc"):
            await mcp_client.stop_gc()

    @pytest.mark.asyncio
    async def test_start_gc_is_idempotent(self, mcp_client):
        """Повторный вызов start_gc() не должен создавать дублирующий task."""
        await mcp_client.start_gc(interval_seconds=1)
        task1 = mcp_client._gc_task

        await mcp_client.start_gc(interval_seconds=1)
        task2 = mcp_client._gc_task

        assert task1 is task2, (
            "\n\n❌ TDD FAIL: start_gc() создал дублирующий GC task.\n"
            "Повторный вызов должен быть no-op"
        )

        # Cleanup
        if hasattr(mcp_client, "stop_gc"):
            await mcp_client.stop_gc()

    @pytest.mark.asyncio
    async def test_stop_gc_cancels_task(self, mcp_client):
        """stop_gc() должен отменять GC task."""
        await mcp_client.start_gc(interval_seconds=1)
        assert mcp_client._gc_task is not None

        gc_task = mcp_client._gc_task
        await mcp_client.stop_gc()

        assert gc_task.done(), (
            "\n\n❌ TDD FAIL: stop_gc() не отменил GC task.\n"
            "После stop_gc task должен быть cancelled"
        )
