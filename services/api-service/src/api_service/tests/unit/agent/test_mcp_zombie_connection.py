"""Regression tests for the MCP event-loop busy-spin.

Root cause (reproduced in Docker on 2026-08-26): killing mcp-gateway while a
tool call is in flight leaves the SDK's streamable-HTTP task group alive after
cancellation is suppressed. The orphaned task keeps re-scheduling itself and
the uvicorn event loop spins one core at 100% forever, invisible to
``/health`` (which kept returning 200 the whole time).

Fix under test:
1. ``_execute_tool_call`` counts consecutive timeouts per connection and
   quarantines (force-closes + forgets) a "zombie-haunted" connection instead
   of reusing it.
2. ``_TenantConnection.close()`` bounds ``session_ctx.__aexit__`` with an
   escalation timeout and force-closes the underlying httpx2 client when the
   SDK refuses to unwind, so suppressed cancellations lose their sockets.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from api_service.agent import mcp_client as mcp_client_module
from api_service.agent.mcp_client import MCPClient, _TenantConnection


def _make_connection(**overrides) -> _TenantConnection:
    defaults = dict(
        tenant_id="test",
        session=MagicMock(),
        session_ctx=MagicMock(),
        transport_http_client=MagicMock(),
    )
    defaults.update(overrides)
    conn = _TenantConnection(**defaults)
    conn.spawn_owner()
    return conn


class TestZombieEscalation:
    """Consecutive tool-call timeouts must quarantine the connection."""

    @pytest.fixture(autouse=True)
    def _short_timeout(self, monkeypatch):
        """Make the internal asyncio.wait timeout fire before outer wait_for."""
        monkeypatch.setattr(
            mcp_client_module.settings, "mcp_tool_execution_timeout", 0.05
        )

    @pytest.mark.asyncio
    async def test_timeout_increments_counter(self):
        client = MCPClient()
        conn = _make_connection()

        async def hanging_call(*args, **kwargs):
            await asyncio.Event().wait()  # never finishes

        conn.session.call_tool = hanging_call

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                client._execute_tool_call(conn, "db_map", {}, ["t"]),
                timeout=10,
            )

        assert conn.consecutive_tool_timeouts == 1

    @pytest.mark.asyncio
    async def test_success_resets_counter(self):
        client = MCPClient()
        conn = _make_connection()
        conn.consecutive_tool_timeouts = 2  # already on the edge

        result = MagicMock()
        result.content = [MagicMock(type="text", text="ok")]

        async def quick_call(*args, **kwargs):
            return result

        conn.session.call_tool = quick_call

        out = await client._execute_tool_call(conn, "db_map", {}, ["t"])
        assert out is result
        assert conn.consecutive_tool_timeouts == 0


class TestBoundedClose:
    """session_ctx.__aexit__ hangs -> escalation must break the loop."""

    @pytest.fixture(autouse=True)
    def _short_close_timeout(self, monkeypatch):
        monkeypatch.setattr(
            mcp_client_module.settings, "mcp_close_escalation_timeout", 0.2
        )

    @pytest.mark.asyncio
    async def test_hanging_exit_force_closes_transport(self):
        hang = asyncio.Event()
        entered = asyncio.Event()

        class HangingCtx:
            async def __aenter__(self):
                entered.set()
                return MagicMock()

            async def __aexit__(self, *exc):
                await hang.wait()  # never returns normally

        conn = _make_connection(session_ctx=HangingCtx())

        aclose_calls = 0

        async def aclose():
            nonlocal aclose_calls
            aclose_calls += 1
            hang.set()

        conn.transport_http_client.aclose = aclose  # type: ignore[assignment]

        await asyncio.wait_for(
            conn.close(),
            timeout=mcp_client_module.settings.mcp_close_escalation_timeout * 4,
        )
        assert aclose_calls > 0

    @pytest.mark.asyncio
    async def test_fast_exit_closes_client_once(self):
        conn = _make_connection()
        ctx = MagicMock()
        ctx.__aexit__ = AsyncMock(return_value=None)
        conn.session_ctx = ctx

        await asyncio.wait_for(conn.close(), timeout=10)

        ctx.__aexit__.assert_awaited_once_with(None, None, None)
        conn.transport_http_client.aclose.assert_called()


class TestQuarantineRemovesFromRegistry:
    async def test_quarantine_removes_exact_connection(self):
        client = MCPClient()
        keep = _make_connection(tenant_id="a")
        kill = _make_connection(tenant_id="a")
        client._connections[""] = keep  # same tenant key position
        client._connections["dup"] = kill

        await asyncio.wait_for(client._quarantine_connection(kill), timeout=10)

        assert "dup" not in client._connections
        assert client._connections.get("") is keep
