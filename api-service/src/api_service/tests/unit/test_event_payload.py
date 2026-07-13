"""Tests for _event_payload — SSE payload builder with display_name support."""

from __future__ import annotations

from typing import Any

from api_service.server import _event_payload


class TestEventPayloadToolCall:
    """_event_payload returns correct shape for tool_call events."""

    def test_tool_call_basic(self):
        """tool_call without display_name falls back to name."""
        data: dict[str, Any] = {
            "id": "c1",
            "name": "find_student",
            "arguments": {"name": "Alice"},
        }
        result = _event_payload("tool_call", data)
        assert result is not None
        assert result["type"] == "tool_call"
        assert result["name"] == "find_student"
        assert result["display_name"] == "find_student"  # fallback to name
        assert "arguments" not in result  # не отдаётся браузеру

    def test_tool_call_with_display_name(self):
        """tool_call with explicit display_name."""
        data: dict[str, Any] = {
            "id": "c1",
            "name": "find_catalog_brand",
            "display_name": "Поиск брендов в каталоге",
            "arguments": {"name": "Bosch"},
        }
        result = _event_payload("tool_call", data)
        assert result is not None
        assert result["name"] == "find_catalog_brand"
        assert result["display_name"] == "Поиск брендов в каталоге"

    def test_tool_call_empty_name(self):
        """tool_call with empty name returns empty display_name."""
        data: dict[str, Any] = {"id": "c1", "name": "", "arguments": {}}
        result = _event_payload("tool_call", data)
        assert result is not None
        assert result["display_name"] == ""


class TestEventPayloadToolResult:
    """_event_payload returns correct shape for tool_result events."""

    def test_tool_result_basic(self):
        """tool_result without display_name falls back to name."""
        data: dict[str, Any] = {
            "id": "c1",
            "name": "find_student",
            "result": '{"data": "ok"}',
        }
        result = _event_payload("tool_result", data)
        assert result is not None
        assert result["type"] == "tool_result"
        assert result["name"] == "find_student"
        assert result["display_name"] == "find_student"
        assert result["result"] == '{"data": "ok"}'

    def test_tool_result_with_display_name(self):
        """tool_result with explicit display_name."""
        data: dict[str, Any] = {
            "id": "c1",
            "name": "find_catalog_brand",
            "display_name": "Поиск брендов в каталоге",
            "result": "[]",
        }
        result = _event_payload("tool_result", data)
        assert result is not None
        assert result["name"] == "find_catalog_brand"
        assert result["display_name"] == "Поиск брендов в каталоге"
        assert result["result"] == "[]"

    def test_tool_result_no_result(self):
        """tool_result with None result omits result field."""
        data: dict[str, Any] = {
            "id": "c1",
            "name": "find_product",
            "display_name": "Поиск товаров",
        }
        result = _event_payload("tool_result", data)
        assert result is not None
        assert result["display_name"] == "Поиск товаров"
        assert "result" not in result


class TestEventPayloadOther:
    """Other event types are unaffected."""

    def test_token(self):
        """token event returns correct payload."""
        result = _event_payload("token", {"data": "hello"})
        assert result == {"type": "token", "text": "hello"}

    def test_final(self):
        """final event returns correct payload."""
        result = _event_payload("final", {"content": "Answer"})
        assert result == {"type": "final", "text": "Answer"}

    def test_error(self):
        """error event returns correct payload."""
        result = _event_payload("error", {"message": "oops"})
        assert result == {"type": "error", "text": "oops"}

    def test_unknown_event(self):
        """Unknown event type returns None."""
        result = _event_payload("unknown", {})
        assert result is None

    def test_empty_data(self):
        """Empty data dict for tool_call."""
        result = _event_payload("tool_call", {})
        assert result is not None
        assert result["name"] == ""
        assert result["display_name"] == ""
