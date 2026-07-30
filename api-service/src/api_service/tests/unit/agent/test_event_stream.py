"""Tests for event_stream — SSE formatting and suffix utilities."""

from __future__ import annotations

from api_service.agent.event_stream import format_sse_event
from api_service.agent.types import AgentEvent


# ── format_sse_event ─────────────────────────────────────────────────────────


class TestFormatSSEEvent:
    """Tests for the format_sse_event() function."""

    def test_format_token_event(self):
        """token event produces correct SSE format."""
        event = AgentEvent("token", {"data": "hello"})
        result = format_sse_event(event)
        assert result == 'event: token\ndata: {"data": "hello"}\n\n'

    def test_format_final_event(self):
        """final event with complex data."""
        event = AgentEvent("final", {"content": "Hello world"})
        result = format_sse_event(event)
        assert "event: final" in result
        assert '"content": "Hello world"' in result
        assert result.endswith("\n\n")

    def test_format_tool_call_event(self):
        """tool_call event."""
        event = AgentEvent("tool_call", {"id": "c1", "name": "foo", "arguments": {}})
        result = format_sse_event(event)
        assert result.startswith("event: tool_call")
        assert '"name": "foo"' in result
        assert result.endswith("\n\n")

    def test_format_error_event(self):
        """error event."""
        event = AgentEvent("error", {"message": "something broke"})
        result = format_sse_event(event)
        assert "event: error" in result
        assert '"message": "something broke"' in result

    def test_unicode_russian(self):
        """Russian text in event data."""
        event = AgentEvent("token", {"data": "Привет, мир!"})
        result = format_sse_event(event)
        assert "Привет, мир!" in result
        assert result.endswith("\n\n")

    def test_trailing_newlines(self):
        """Every event ends with exactly \n\n."""
        event = AgentEvent(
            "status", {"phase": "tool_calls", "iteration": 0, "count": 2}
        )
        result = format_sse_event(event)
        assert result.endswith("\n\n")
        # Should have at least one \n\n and end with it
        assert "\n\n" in result
        # The result should NOT have 3 consecutive newlines
        assert "\n\n\n" not in result

    def test_empty_data_string(self):
        """Empty data string produces valid SSE."""
        event = AgentEvent("token", {"data": ""})
        result = format_sse_event(event)
        assert '"data": ""' in result
