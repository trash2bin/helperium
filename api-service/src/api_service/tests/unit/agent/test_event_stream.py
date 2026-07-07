"""Tests for event_stream — SSE formatting and suffix utilities."""

from __future__ import annotations

from api_service.agent.event_stream import format_sse_event, unstreamed_suffix
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


# ── unstreamed_suffix ────────────────────────────────────────────────────────


class TestUnstreamedSuffix:
    """Tests for the unstreamed_suffix() function."""

    def test_empty_streamed_returns_full(self):
        """When nothing was streamed, return full text."""
        assert unstreamed_suffix("", "Hello world") == "Hello world"

    def test_exact_match_returns_empty(self):
        """When streamed equals final, return empty string."""
        assert unstreamed_suffix("Hello", "Hello") == ""

    def test_prefix_returns_remainder(self):
        """When streamed is a proper prefix, return the suffix."""
        assert unstreamed_suffix("Hello, ", "Hello, world!") == "world!"

    def test_not_a_prefix_returns_empty(self):
        """When streamed is not a prefix of final, return ''."""
        assert unstreamed_suffix("Hxllo", "Hello") == ""

    def test_russian_text(self):
        """Russian text handled correctly."""
        assert unstreamed_suffix("Привет, ", "Привет, мир!") == "мир!"

    def test_empty_both(self):
        """Both empty returns empty."""
        assert unstreamed_suffix("", "") == ""

    def test_streamed_longer_than_final(self):
        """When streamed is not a prefix (longer), return empty."""
        assert unstreamed_suffix("Hello!!", "Hello") == ""
