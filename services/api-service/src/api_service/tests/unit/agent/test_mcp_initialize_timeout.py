"""
Ensures the MCP v2 client does not hang indefinitely while entering a legacy
SSE connection.  The v2 ``Client`` owns transport setup and initialization,
so these tests mock its async context-manager boundary rather than raw
``ClientSession`` streams.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api_service.agent.mcp_client import MCPClient


class TestMCPInitializeTimeout:
    """``MCPClient._open_connection`` must respect the connection deadline."""

    @pytest.mark.asyncio
    async def test_initialize_timeout_with_slow_gateway(self):
        """A stalled v2 Client entry must fail within the configured timeout."""
        client = MCPClient()

        async def never_connect(*args, **kwargs):
            await asyncio.Event().wait()

        client_ctx = MagicMock()
        client_ctx.__aenter__ = never_connect
        client_ctx.__aexit__ = AsyncMock(return_value=None)

        with patch("api_service.agent.mcp_client.Client", return_value=client_ctx):
            with pytest.raises((TimeoutError, asyncio.TimeoutError)):
                async with asyncio.timeout(20):
                    await client._open_connection(["test-tenant"])

        client_ctx.__aexit__.assert_awaited_once_with(None, None, None)

    @pytest.mark.asyncio
    async def test_initialize_timeout_fast_fallback(self):
        """The MCP session-init deadline bounds an indefinitely slow connect."""
        client = MCPClient()

        async def slow_client_enter(*args, **kwargs):
            await asyncio.sleep(30)
            return MagicMock()

        client_ctx = MagicMock()
        client_ctx.__aenter__ = slow_client_enter
        client_ctx.__aexit__ = AsyncMock(return_value=None)

        with patch("api_service.agent.mcp_client.Client", return_value=client_ctx):
            t0 = asyncio.get_event_loop().time()
            with pytest.raises((TimeoutError, asyncio.TimeoutError)):
                async with asyncio.timeout(20):
                    await client._open_connection(["test-tenant"])
            elapsed = asyncio.get_event_loop().time() - t0

        assert elapsed < 25, (
            f"_open_connection выполнялся {elapsed:.1f}s — session init timeout не сработал"
        )

    @pytest.mark.asyncio
    async def test_normal_initialize_passes(self):
        """A normally connected v2 Client produces a reusable tenant connection."""
        client = MCPClient()
        mock_session = AsyncMock()
        client_ctx = MagicMock()
        client_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        client_ctx.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("api_service.agent.mcp_client.Client", return_value=client_ctx),
            patch(
                "api_service.agent.mcp_client.settings.mcp_gateway_url",
                "http://localhost:9999",
            ),
            patch(
                "api_service.agent.mcp_client.settings.mcp_streamable_http_url",
                "http://localhost:9999/mcp",
            ),
            patch("api_service.agent.mcp_client.httpx.AsyncClient"),
            patch("api_service.agent.mcp_client.httpx2.AsyncClient"),
        ):
            conn = await client._open_connection(["test-tenant"])

        assert conn is not None
        assert conn.session is mock_session
        client_ctx.__aenter__.assert_awaited_once()
