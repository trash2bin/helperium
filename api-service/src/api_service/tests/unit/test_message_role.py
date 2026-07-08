"""Tests for MessageRole and Message types using StrEnum."""

from __future__ import annotations

from typing import get_args


from api_service.agent.types import (
    MessageRole,
    SystemMessage,
    UserMessage,
    AssistantMessage,
    ToolMessage,
    Message,
    EventType,
)


class TestMessageRoleIsStrEnum:
    """MessageRole should be StrEnum, not bare str."""

    def test_role_values_are_strings(self):
        assert MessageRole.SYSTEM == "system"
        assert MessageRole.USER == "user"
        assert MessageRole.ASSISTANT == "assistant"
        assert MessageRole.TOOL == "tool"

    def test_role_comparison_with_string(self):
        assert MessageRole.SYSTEM == "system"

    def test_role_is_enum_member(self):
        import enum

        assert isinstance(MessageRole.SYSTEM, enum.Enum)

    def test_role_is_str(self):
        assert isinstance(MessageRole.SYSTEM, str)

    def test_all_roles_have_correct_type(self):
        for role in MessageRole:
            assert isinstance(role, str)


class TestMessageTypes:
    """Message TypedDicts should work with enum roles."""

    def test_system_message(self):
        msg: SystemMessage = {
            "role": MessageRole.SYSTEM,
            "content": "You are a helpful assistant.",
        }
        assert msg["role"] == "system"

    def test_user_message(self):
        msg: UserMessage = {
            "role": MessageRole.USER,
            "content": "Hello!",
        }
        assert msg["role"] == "user"

    def test_assistant_message(self):
        msg: AssistantMessage = {
            "role": MessageRole.ASSISTANT,
            "content": "Hi!",
        }
        assert msg["role"] == "assistant"

    def test_tool_message(self):
        msg: ToolMessage = {
            "role": MessageRole.TOOL,
            "content": '{"result": "ok"}',
            "tool_call_id": "call_123",
            "name": "get_weather",
        }
        assert msg["role"] == "tool"

    def test_message_union(self):
        """All message types should be valid Message union members."""
        msgs: list[Message] = [
            SystemMessage(role=MessageRole.SYSTEM, content="sys"),
            UserMessage(role=MessageRole.USER, content="user"),
            AssistantMessage(role=MessageRole.ASSISTANT, content="asst"),
            ToolMessage(
                role=MessageRole.TOOL,
                content="tool",
                tool_call_id="c1",
                name="test",
            ),
        ]
        assert len(msgs) == 4

    def test_message_role_is_str_enum_pair(self):
        """Verify MessageRole is both str and Enum."""
        import enum

        assert issubclass(MessageRole, enum.Enum)
        assert issubclass(MessageRole, str)


class TestEventType:
    """EventType Literal should match the defined values."""

    def test_event_types(self):
        types = get_args(EventType)
        assert "status" in types
        assert "token" in types
        assert "tool_call" in types
        assert "tool_result" in types
        assert "final" in types
        assert "error" in types
