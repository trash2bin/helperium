"""Tests for MCPClient call_lock timeout behaviour.

These tests verify that list_tools and call_tool handle lock acquisition
timeout gracefully by returning the appropriate error ToolResult.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api_service.agent.mcp_client import MCPClient


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_conn(
    call_lock: asyncio.Lock | None = None,
    list_lock: asyncio.Lock | None = None,
) -> MagicMock:
    """Build a mock _TenantConnection with controlled locks."""
    conn = MagicMock()
    conn.tenant_id = "test-tenant"
    conn.call_lock = call_lock or asyncio.Lock()
    conn.list_lock = list_lock or asyncio.Lock()
    conn.session = AsyncMock()
    return conn


async def _session_proxy(client: MCPClient, tenant_ids: list[str] | None = None):
    """Convenience: create a fresh _SessionProxy for the given client."""
    from api_service.agent.mcp_client import _SessionProxy

    return _SessionProxy(client, tenant_ids=tenant_ids or [])


# ── Tests: call_tool lock timeout ────────────────────────────────────────────


@pytest.mark.asyncio
@patch("helperium_sdk.settings.settings.mcp_lock_acquire_timeout", 0.05)
@patch("helperium_sdk.settings.settings.mcp_tool_execution_timeout", 5.0)
async def test_call_tool_lock_timeout():
    """call_tool should return error ToolResult when lock cannot be acquired."""
    client = MCPClient()

    # A lock that is already held → acquire() blocks → triggers timeout on LOCK_ACQUIRE_TIMEOUT
    held_lock = asyncio.Lock()
    await held_lock.acquire()

    conn = _make_conn(call_lock=held_lock)
    client._get_connection = AsyncMock(return_value=conn)  # type: ignore[method-assign]

    session = await _session_proxy(client)
    result = await client.call_tool(session, "test_tool", {"arg": 1})

    assert result.ok is False
    assert result.error is not None
    assert "timed out" in result.error
    assert "test_tool" in result.reminder


@pytest.mark.asyncio
async def test_call_tool_lock_acquires_normally():
    """call_tool should work normally when lock is available."""
    client = MCPClient()
    conn = _make_conn()
    conn.session.call_tool = AsyncMock(
        return_value=MagicMock(
            content=[MagicMock(type="text", text='{"ok": true, "data": "hello"}')],
            is_error=False,
        )
    )
    client._get_connection = AsyncMock(return_value=conn)  # type: ignore[method-assign]

    session = await _session_proxy(client)
    result = await client.call_tool(session, "greet", {"who": "world"})

    assert result.ok is True
    conn.session.call_tool.assert_awaited_once_with("greet", {"who": "world"})


@pytest.mark.asyncio
@patch("helperium_sdk.settings.settings.mcp_tool_execution_timeout", 0.05)
async def test_call_tool_execution_hard_deadline_when_sdk_suppresses_cancellation():
    """A reconnecting transport must not keep an SSE request alive indefinitely."""
    client = MCPClient()
    conn = _make_conn()
    conn.consecutive_tool_timeouts = 0  # real int for zombie-escalation check
    cancellation_seen = asyncio.Event()
    allow_task_exit = asyncio.Event()

    async def cancellation_suppressing_call(*_args, **_kwargs):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancellation_seen.set()
            await allow_task_exit.wait()
            return MagicMock(content=[], is_error=True)

    conn.session.call_tool = AsyncMock(side_effect=cancellation_suppressing_call)
    client._get_connection = AsyncMock(return_value=conn)  # type: ignore[method-assign]

    session = await _session_proxy(client)
    result = await client.call_tool(session, "stalled_tool", {})

    assert result.ok is False
    assert "timed out" in (result.error or "")
    await asyncio.wait_for(cancellation_seen.wait(), timeout=0.5)
    allow_task_exit.set()
    await asyncio.sleep(0)


# ── Tests: list_tools lock timeout ───────────────────────────────────────────


@pytest.mark.asyncio
@patch("helperium_sdk.settings.settings.mcp_lock_acquire_timeout", 0.05)
async def test_list_tools_lock_timeout():
    """A lock-phase timeout in list_tools degrades to an empty tool list.

    Symmetric with call_tool sanitisation and the cold-handshake path:
    dependency failures must never leak raw exceptions into the agent loop.
    Previously the lock timeout re-raised TimeoutError into the loop.
    """
    client = MCPClient()

    held_lock = asyncio.Lock()
    await held_lock.acquire()

    conn = _make_conn(list_lock=held_lock)
    client._get_connection = AsyncMock(return_value=conn)  # type: ignore[method-assign]

    session = await _session_proxy(client)

    tools = await client.list_tools(session)
    assert tools == []
    assert session.list_tools_failed is True


@pytest.mark.asyncio
async def test_list_tools_lock_acquires_normally():
    """list_tools should work normally when lock is available."""
    client = MCPClient()
    conn = _make_conn()
    mock_tool = MagicMock()
    mock_tool.name = "get_student"
    mock_tool.description = "Get student info"
    mock_tool.input_schema = {
        "type": "object",
        "properties": {"id": {"type": "string"}},
    }
    conn.session.list_tools = AsyncMock(return_value=MagicMock(tools=[mock_tool]))
    client._get_connection = AsyncMock(return_value=conn)  # type: ignore[method-assign]

    session = await _session_proxy(client)
    tools = await client.list_tools(session)

    assert len(tools) == 1
    assert tools[0]["function"]["name"] == "get_student"
    assert tools[0]["function"]["description"] == "Get student info"
    assert session.list_tools_failed is False
