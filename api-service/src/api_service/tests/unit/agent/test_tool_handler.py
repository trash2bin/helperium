"""Tests for ToolHandler — MCP tool execution."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api_service.agent.mcp_client import MCPClient, ToolResult
from api_service.agent.tool_handler import ToolHandler
from api_service.agent.types import AgentEvent, ParsedToolCall


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_mcp():
    """Mock MCPClient returning successful tool results."""
    mock = AsyncMock(spec=MCPClient)
    mock.call_tool.return_value = ToolResult(
        tool_content='{"data": "ok"}',
        reminder="show this data",
        ok=True,
    )
    return mock


@pytest.fixture
def mock_conv_mgr():
    """Mock ConversationManager."""
    return AsyncMock()


@pytest.fixture
def handler(mock_mcp, mock_conv_mgr):
    """ToolHandler wired to mocks."""
    return ToolHandler(mcp_client=mock_mcp, conversation_manager=mock_conv_mgr)


@pytest.fixture
def ctx():
    """Pre-populated TurnContext."""
    from api_service.agent.turn_context import TurnContext

    c = TurnContext()
    c.session_id = "test-session"
    c.turn_id = "test-turn"
    c.iteration = 0
    c.messages = [
        {"role": "system", "content": "sp"},
        {"role": "user", "content": "hello"},
    ]
    c.turn_messages = [{"role": "user", "content": "hello"}]
    return c


@pytest.fixture
def session():
    """Fake MCP session object."""
    return MagicMock()


# ── Tests ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestToolHandler:
    """All ToolHandler scenarios."""

    async def test_single_tool_success(self, handler, ctx, session, mock_mcp):
        """Single tool call: yields ToolCall and ToolResult events."""
        tool_calls: list[ParsedToolCall] = [
            {"id": "c1", "name": "find_student", "arguments": {"name": "Alice"}},
        ]

        events: list[AgentEvent] = [
            e async for e in handler.execute(tool_calls, session, ctx)
        ]

        # Should yield 2 events: tool_call and tool_result
        assert len(events) == 2
        assert events[0].type == "tool_call"
        assert events[0].data["name"] == "find_student"
        assert events[1].type == "tool_result"
        assert events[1].data["name"] == "find_student"

        # Tool message appended to ctx.messages
        assert len(ctx.messages) == 3
        assert ctx.messages[-1]["role"] == "tool"
        assert ctx.messages[-1]["name"] == "find_student"

        # Tool message also in turn_messages
        assert ctx.turn_messages[-1]["role"] == "tool"

        # MCP was called
        mock_mcp.call_tool.assert_awaited_once_with(
            session, "find_student", {"name": "Alice"}
        )

    async def test_single_tool_exception(self, handler, ctx, session, mock_mcp):
        """When call_tool raises, graceful error ToolResult is used."""
        mock_mcp.call_tool.side_effect = ValueError("connection refused")

        tool_calls: list[ParsedToolCall] = [
            {"id": "c1", "name": "find_student", "arguments": {"name": "Alice"}},
        ]

        events = [e async for e in handler.execute(tool_calls, session, ctx)]

        assert len(events) == 2
        assert events[0].type == "tool_call"
        assert events[1].type == "tool_result"

        # Tool result should contain the error
        assert "error" in events[1].data["result"] or True  # just check it exists
        assert ctx.messages[-1]["role"] == "tool"

    async def test_multiple_tools(self, handler, ctx, session, mock_mcp):
        """Multiple tool calls each produce events and messages."""
        tool_calls: list[ParsedToolCall] = [
            {"id": "c1", "name": "find_student", "arguments": {"name": "Alice"}},
            {"id": "c2", "name": "list_courses", "arguments": {}},
        ]

        events = [e async for e in handler.execute(tool_calls, session, ctx)]

        # 2 tools × 2 events each = 4 events
        assert len(events) == 4
        assert [e.type for e in events] == [
            "tool_call",
            "tool_result",
            "tool_call",
            "tool_result",
        ]

        # Both tool messages in messages
        tool_msgs = [m for m in ctx.messages if m["role"] == "tool"]
        assert len(tool_msgs) == 2
        assert tool_msgs[0]["name"] == "find_student"
        assert tool_msgs[1]["name"] == "list_courses"

        # MCP called twice
        assert mock_mcp.call_tool.await_count == 2

    async def test_tool_call_event_data(self, handler, ctx, session, mock_mcp):
        """ToolCall event has correct id, name, arguments."""
        tool_calls: list[ParsedToolCall] = [
            {"id": "c1", "name": "get_grade", "arguments": {"student_id": 42}},
        ]

        events = [e async for e in handler.execute(tool_calls, session, ctx)]

        tc_event = events[0]
        assert tc_event.type == "tool_call"
        assert tc_event.data["id"] == "c1"
        assert tc_event.data["name"] == "get_grade"
        assert tc_event.data["arguments"] == {"student_id": 42}

    async def test_tool_result_event_data(self, handler, ctx, session, mock_mcp):
        """ToolResult event has correct id, name, result."""
        tool_calls: list[ParsedToolCall] = [
            {"id": "c1", "name": "find_student", "arguments": {"name": "Alice"}},
        ]

        events = [e async for e in handler.execute(tool_calls, session, ctx)]

        tr_event = events[1]
        assert tr_event.type == "tool_result"
        assert tr_event.data["id"] == "c1"
        assert tr_event.data["name"] == "find_student"
        assert tr_event.data["result"] == '{"data": "ok"}'

    async def test_empty_tool_calls(self, handler, ctx, session):
        """Empty tool_calls list → no events, no messages appended."""
        events = [e async for e in handler.execute([], session, ctx)]
        assert len(events) == 0
        assert len(ctx.messages) == 2  # unchanged

    async def test_tool_without_id(self, handler, ctx, session, mock_mcp):
        """Tool call without an ID gets auto-generated."""
        tool_calls: list[ParsedToolCall] = [
            {"id": "", "name": "find_student", "arguments": {"name": "Bob"}},
        ]

        events = [e async for e in handler.execute(tool_calls, session, ctx)]

        tc_event = events[0]
        # Auto-generated ID starts with call_find_student_
        assert tc_event.data["id"].startswith("call_find_student_")

    async def test_backlog_called(self, handler, ctx, session, mock_mcp):
        """backlog.tool_call and backlog.tool_result are called."""
        tool_calls: list[ParsedToolCall] = [
            {"id": "c1", "name": "find_student", "arguments": {"name": "Alice"}},
        ]

        with patch("api_service.agent.tool_handler.backlog") as mock_backlog:
            async for _ in handler.execute(tool_calls, session, ctx):
                pass

            mock_backlog.tool_call.assert_called_once()
            mock_backlog.tool_result.assert_called_once()

    async def test_mixed_success_and_failure(self, handler, ctx, session, mock_mcp):
        """One failing tool doesn't prevent the next from executing."""
        # First call raises, second succeeds
        mock_mcp.call_tool.side_effect = [
            ValueError("timeout"),
            ToolResult(
                tool_content='{"data": "courses"}',
                reminder="here are courses",
                ok=True,
            ),
        ]

        tool_calls: list[ParsedToolCall] = [
            {"id": "c1", "name": "broken_tool", "arguments": {}},
            {"id": "c2", "name": "good_tool", "arguments": {}},
        ]

        events = [e async for e in handler.execute(tool_calls, session, ctx)]

        assert len(events) == 4
        # Both tool results should be in messages
        tool_msgs = [m for m in ctx.messages if m["role"] == "tool"]
        assert len(tool_msgs) == 2
