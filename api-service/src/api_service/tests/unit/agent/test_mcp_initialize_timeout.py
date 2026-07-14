"""Контрактный тест #3: MCP initialize timeout.

Проверяет что MCP client не висит вечно если sse_client упал.
Использует mock на уровне sse_client, чтобы не требовать реального MCP gateway.

Related: api-service/src/api_service/agent/mcp_client.py _open_connection()
Добавленный asyncio.timeout(15) на initialize() (PR #...)
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from api_service.agent.mcp_client import MCPClient


class TestMCPInitializeTimeout:
    """MCPClient._open_connection должен падать с таймаутом, а не виснуть."""

    @pytest.mark.asyncio
    async def test_initialize_timeout_with_slow_gateway(self):
        """Если initialize() не отвечает >15с, клиент падает с TimeoutError.

        Mock'аем sse_client так, что initialize() никогда не завершается.
        """
        client = MCPClient()

        # Создаём read_stream/write_stream с вечным initialize
        read_stream = AsyncMock()
        write_stream = AsyncMock()

        async def never_ending_initialize(*args, **kwargs):
            """Simulate a gateway that never responds."""
            await asyncio.Event().wait()  # hangs forever

        mock_session = AsyncMock()
        mock_session.initialize = never_ending_initialize

        # sse_client возвращает рабочую пару стримов
        sse_ctx = AsyncMock()
        sse_ctx.__aenter__ = AsyncMock(return_value=(read_stream, write_stream))

        # session_ctx возвращает session с вечным initialize
        session_ctx = AsyncMock()
        session_ctx.__aenter__ = AsyncMock(return_value=mock_session)

        with (
            patch("api_service.agent.mcp_client.sse_client", return_value=sse_ctx),
            patch(
                "api_service.agent.mcp_client.ClientSession",
                return_value=session_ctx,
            ),
        ):
            with pytest.raises((TimeoutError, asyncio.TimeoutError)):
                async with asyncio.timeout(20):  # safety net
                    await client._open_connection(["test-tenant"])

    @pytest.mark.asyncio
    async def test_initialize_timeout_fast_fallback(self):
        """Timeout срабатывает ~15с (не 30+), даже если sse_client тоже медленный."""
        client = MCPClient()

        # sse_client сам медленный — долго открывает соединение
        async def slow_sse_enter(*args, **kwargs):
            await asyncio.sleep(30)
            return (AsyncMock(), AsyncMock())

        sse_ctx = AsyncMock()
        sse_ctx.__aenter__ = slow_sse_enter

        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()

        session_ctx = AsyncMock()
        session_ctx.__aenter__ = AsyncMock(return_value=mock_session)

        with (
            patch("api_service.agent.mcp_client.sse_client", return_value=sse_ctx),
            patch(
                "api_service.agent.mcp_client.ClientSession",
                return_value=session_ctx,
            ),
        ):
            t0 = asyncio.get_event_loop().time()
            with pytest.raises((TimeoutError, asyncio.TimeoutError)):
                async with asyncio.timeout(20):  # safety net
                    await client._open_connection(["test-tenant"])
            elapsed = asyncio.get_event_loop().time() - t0
            # Должно упасть из-за sse_timeout=10 (sse_client), не ждать 30с
            assert elapsed < 25, (
                f"_open_connection выполнялся {elapsed:.1f}s — sse timeout не сработал"
            )

    @pytest.mark.asyncio
    async def test_normal_initialize_passes(self):
        """Нормальный initialize() проходит, таймаут не срабатывает."""
        client = MCPClient()

        read_stream = AsyncMock()
        write_stream = AsyncMock()

        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock(return_value=None)

        sse_ctx = AsyncMock()
        sse_ctx.__aenter__ = AsyncMock(return_value=(read_stream, write_stream))

        session_ctx = AsyncMock()
        session_ctx.__aenter__ = AsyncMock(return_value=mock_session)

        with (
            patch("api_service.agent.mcp_client.sse_client", return_value=sse_ctx),
            patch(
                "api_service.agent.mcp_client.ClientSession",
                return_value=session_ctx,
            ),
            patch(
                "api_service.agent.mcp_client.settings.mcp_service_url",
                "http://localhost:9999",
            ),
            patch(
                "api_service.agent.mcp_client.httpx.AsyncClient",
            ),
        ):
            conn = await client._open_connection(["test-tenant"])
            assert conn is not None
            mock_session.initialize.assert_awaited_once()
