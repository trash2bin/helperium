"""Unit tests for MCPClient.

Tests the MCP SDK-based client by mocking _get_connection to avoid
real SSE connections. Follows the same pattern as test_mcp_client_timeout.py.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from api_service.agent.mcp_client import MCPClient


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_conn() -> MagicMock:
    """Build a mock _TenantConnection with a mock session.

    Both ``call_lock`` and ``list_lock`` are mocked so that
    ``async with`` context-manager calls succeed immediately.
    """
    conn = MagicMock()
    conn.tenant_id = "test-tenant"
    conn.session = AsyncMock()
    conn.call_lock = MagicMock()
    conn.call_lock.__aenter__ = AsyncMock()
    conn.call_lock.__aexit__ = AsyncMock(return_value=None)
    conn.list_lock = MagicMock()
    conn.list_lock.__aenter__ = AsyncMock()
    conn.list_lock.__aexit__ = AsyncMock(return_value=None)
    return conn


def _mock_tool(
    name: str, description: str, input_schema: dict | None = None
) -> MagicMock:
    """Create a mock MCP Tool object."""
    tool = MagicMock()
    tool.name = name
    tool.description = description
    tool.inputSchema = input_schema or {"type": "object", "properties": {}}
    return tool


def _mock_result(content_parts: list[dict], is_error: bool = False) -> MagicMock:
    """Create a mock CallToolResult."""
    result = MagicMock()
    result.content = []
    for part in content_parts:
        block = MagicMock()
        block.type = part.get("type", "text")
        block.text = part.get("text", "")
        result.content.append(block)
    result.isError = is_error
    return result


@pytest.fixture
def mcp_client() -> MCPClient:
    return MCPClient()


# ── _build_tool_result tests ─────────────────────────────────────────────────


class TestBuildToolResult:
    """Tests for _build_tool_result — pure static method, no mocking needed."""

    def test_success_text_result(self):
        """Text-only success result should produce ok=True with content."""
        result = _mock_result([{"type": "text", "text": "Student: Ivan"}])
        tr = MCPClient._build_tool_result("get_student", result)
        assert tr.ok is True
        assert tr.error is None
        assert "Student: Ivan" in tr.tool_content
        assert "ОБЯЗАТЕЛЬНО" in tr.reminder

    def test_success_json_unwrap(self):
        """JSON string in text content should be unwrapped."""
        inner = {"id": "1", "name": "Ivan"}
        result = _mock_result([{"type": "text", "text": json.dumps(inner)}])
        tr = MCPClient._build_tool_result("get_student", result)
        assert tr.ok is True
        parsed = json.loads(tr.tool_content)
        assert parsed == inner

    def test_error_result(self):
        """isError=True should produce ok=False with error message."""
        result = _mock_result(
            [{"type": "text", "text": "Student not found"}], is_error=True
        )
        tr = MCPClient._build_tool_result("find_student", result)
        assert tr.ok is False
        assert tr.error == "Student not found"
        assert "TOOL_ERROR" in tr.reminder
        assert "'find_student'" in tr.reminder
        assert "FAILED" in tr.reminder

    def test_empty_result(self):
        """Empty or null text should produce ok=True with data=None."""
        for empty in ["", "null"]:
            result = _mock_result([{"type": "text", "text": empty}])
            tr = MCPClient._build_tool_result("find_student", result)
            assert tr.ok is True
            assert "записи нет" in tr.reminder

    def test_none_string_result(self):
        """The literal 'None' string is valid JSON → parsed as null content."""
        result = _mock_result([{"type": "text", "text": "None"}])
        tr = MCPClient._build_tool_result("find_student", result)
        assert tr.ok is True
        assert tr.tool_content is not None

    def test_multiple_text_blocks(self):
        """Multiple text blocks should be joined with newlines."""
        result = _mock_result(
            [
                {"type": "text", "text": "line1"},
                {"type": "text", "text": "line2"},
            ]
        )
        tr = MCPClient._build_tool_result("get_data", result)
        assert tr.ok is True
        assert "line1\nline2" in tr.tool_content


# ── list_tools tests ─────────────────────────────────────────────────────────


class TestListTools:
    """Tests for MCPClient.list_tools."""

    async def _session(self, client: MCPClient):
        from api_service.agent.mcp_client import _SessionProxy

        return _SessionProxy(client, tenant_ids=[])

    @pytest.mark.asyncio
    async def test_list_tools_success(self, mcp_client: MCPClient):
        """list_tools should return formatted tool dicts."""
        conn = _make_conn()
        conn.session.list_tools = AsyncMock(
            return_value=MagicMock(
                tools=[
                    _mock_tool(
                        "get_student",
                        "Get student info",
                        {"type": "object", "properties": {"id": {"type": "string"}}},
                    ),
                ]
            )
        )
        mcp_client._get_connection = AsyncMock(return_value=conn)  # type: ignore[method-assign]

        session = await self._session(mcp_client)
        tools = await mcp_client.list_tools(session)

        assert len(tools) == 1
        assert tools[0]["function"]["name"] == "get_student"
        assert tools[0]["function"]["description"] == "Get student info"
        assert tools[0]["function"]["parameters"] == {
            "type": "object",
            "properties": {"id": {"type": "string"}},
        }

    @pytest.mark.asyncio
    async def test_list_tools_reconnect_on_failure(self, mcp_client: MCPClient):
        """list_tools should reconnect on first failure and retry."""
        conn = _make_conn()
        conn.session.list_tools = AsyncMock()
        conn.session.list_tools.side_effect = [
            Exception("Connection lost"),
            MagicMock(tools=[_mock_tool("get_student", "Get student info")]),
        ]

        reconn = _make_conn()
        reconn.session.list_tools = AsyncMock(
            return_value=MagicMock(
                tools=[_mock_tool("get_student", "Get student info")]
            )
        )

        mcp_client._get_connection = AsyncMock(return_value=conn)
        mcp_client._reconnect = AsyncMock(return_value=reconn)  # type: ignore[method-assign]

        session = await self._session(mcp_client)
        tools = await mcp_client.list_tools(session)

        assert len(tools) == 1
        assert tools[0]["function"]["name"] == "get_student"
        mcp_client._reconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_list_tools_tool_not_found_returns_empty(self, mcp_client: MCPClient):
        """'Tool not found' error should not trigger reconnect."""
        conn = _make_conn()
        conn.session.list_tools = AsyncMock(side_effect=Exception("Tool not found"))
        mcp_client._get_connection = AsyncMock(return_value=conn)
        mcp_client._reconnect = AsyncMock()

        session = await self._session(mcp_client)
        tools = await mcp_client.list_tools(session)

        assert tools == []
        mcp_client._reconnect.assert_not_called()


# ── call_tool tests ──────────────────────────────────────────────────────────


class TestCallTool:
    """Tests for MCPClient.call_tool."""

    async def _session(self, client: MCPClient):
        from api_service.agent.mcp_client import _SessionProxy

        return _SessionProxy(client, tenant_ids=[])

    @pytest.mark.asyncio
    async def test_call_tool_success(self, mcp_client: MCPClient):
        """call_tool should return ok=True with content."""
        conn = _make_conn()
        conn.session.call_tool = AsyncMock(
            return_value=_mock_result(
                [{"type": "text", "text": "Student: Ivan Ivanov"}]
            )
        )
        mcp_client._get_connection = AsyncMock(return_value=conn)

        session = await self._session(mcp_client)
        tr = await mcp_client.call_tool(session, "get_student", {"id": "1"})

        assert tr.ok is True
        assert tr.error is None
        assert "Student: Ivan Ivanov" in tr.tool_content
        assert "ОБЯЗАТЕЛЬНО" in tr.reminder
        conn.session.call_tool.assert_awaited_once_with("get_student", {"id": "1"})

    @pytest.mark.asyncio
    async def test_call_tool_gateway_error(self, mcp_client: MCPClient):
        """call_tool should return ok=False when tool returns error."""
        conn = _make_conn()
        conn.session.call_tool = AsyncMock(
            return_value=_mock_result(
                [{"type": "text", "text": "Student not found"}], is_error=True
            )
        )
        mcp_client._get_connection = AsyncMock(return_value=conn)

        session = await self._session(mcp_client)
        tr = await mcp_client.call_tool(session, "get_student", {"id": "999"})

        assert tr.ok is False
        assert tr.error == "Student not found"
        assert "TOOL_ERROR" in tr.reminder
        assert "'get_student'" in tr.reminder
        assert "FAILED" in tr.reminder

    @pytest.mark.asyncio
    async def test_call_tool_reconnect_on_failure(self, mcp_client: MCPClient):
        """call_tool should reconnect on non-ToolNotFound error and retry."""
        conn = _make_conn()
        conn.session.call_tool = AsyncMock(side_effect=Exception("Connection lost"))
        mcp_client._get_connection = AsyncMock(return_value=conn)

        reconn = _make_conn()
        reconn.session.call_tool = AsyncMock(
            return_value=_mock_result([{"type": "text", "text": "Retry worked"}])
        )
        mcp_client._reconnect = AsyncMock(return_value=reconn)

        session = await self._session(mcp_client)
        tr = await mcp_client.call_tool(session, "retry_test", {"arg": 1})

        assert tr.ok is True
        assert "Retry worked" in tr.tool_content
        mcp_client._reconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_call_tool_tool_not_found_returns_error(self, mcp_client: MCPClient):
        """'Tool not found' error should NOT trigger reconnect."""
        conn = _make_conn()
        conn.session.call_tool = AsyncMock(side_effect=Exception("Tool not found"))
        mcp_client._get_connection = AsyncMock(return_value=conn)
        mcp_client._reconnect = AsyncMock()

        session = await self._session(mcp_client)
        tr = await mcp_client.call_tool(session, "ghost_tool", {})

        assert tr.ok is False
        assert "Tool not found" in (tr.error or "")
        mcp_client._reconnect.assert_not_called()

    @pytest.mark.asyncio
    async def test_call_tool_unwraps_json_data(self, mcp_client: MCPClient):
        """JSON text content should be unwrapped for cleaner tool_content."""
        inner = {"id": "1", "name": "Ivan"}
        conn = _make_conn()
        conn.session.call_tool = AsyncMock(
            return_value=_mock_result([{"type": "text", "text": json.dumps(inner)}])
        )
        mcp_client._get_connection = AsyncMock(return_value=conn)

        session = await self._session(mcp_client)
        tr = await mcp_client.call_tool(session, "get_student", {"id": "1"})

        parsed = json.loads(tr.tool_content)
        assert parsed == inner

    @pytest.mark.asyncio
    async def test_call_tool_keeps_plain_text(self, mcp_client: MCPClient):
        """Plain text (not JSON) should be kept as-is."""
        conn = _make_conn()
        conn.session.call_tool = AsyncMock(
            return_value=_mock_result([{"type": "text", "text": "plain text response"}])
        )
        mcp_client._get_connection = AsyncMock(return_value=conn)

        session = await self._session(mcp_client)
        tr = await mcp_client.call_tool(session, "greet", {"who": "world"})

        assert "plain text response" in tr.tool_content
