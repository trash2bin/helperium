"""Tests for TurnContext — turn-level state container."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from api_service.agent.turn_context import TurnContext
from api_service.agent.types import ParsedToolCall


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_conv_mgr():
    """ConversationManager with aget_history_messages returning empty history."""
    mgr = AsyncMock()
    mgr.aget_history_messages.return_value = []
    return mgr


@pytest.fixture
def mock_conv_mgr_with_history():
    """ConversationManager with pre-loaded history."""
    mgr = AsyncMock()
    mgr.aget_history_messages.return_value = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    return mgr


# ── Tests for build() ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_build_empty_history(mock_conv_mgr):
    """build() with empty history creates correct messages and turn_messages."""
    ctx = await TurnContext.build(
        user_message="hello!",
        session_id="sess-1",
        system_prompt="You are a bot.",
        conversation_manager=mock_conv_mgr,
    )

    assert len(ctx.messages) == 2
    assert ctx.messages[0] == {"role": "system", "content": "You are a bot."}
    assert ctx.messages[1] == {"role": "user", "content": "hello!"}

    assert len(ctx.turn_messages) == 1
    assert ctx.turn_messages[0] == {"role": "user", "content": "hello!"}

    assert ctx.session_id == "sess-1"
    mock_conv_mgr.aget_history_messages.assert_awaited_once_with("sess-1")


@pytest.mark.asyncio
async def test_build_with_history(mock_conv_mgr_with_history):
    """build() with history inserts history between system and user messages."""
    ctx = await TurnContext.build(
        user_message="how are you?",
        session_id="sess-2",
        system_prompt="You are helpful.",
        conversation_manager=mock_conv_mgr_with_history,
    )

    assert len(ctx.messages) == 4
    assert ctx.messages[0] == {"role": "system", "content": "You are helpful."}
    assert ctx.messages[1] == {"role": "user", "content": "hi"}
    assert ctx.messages[2] == {"role": "assistant", "content": "hello"}
    assert ctx.messages[3] == {"role": "user", "content": "how are you?"}


@pytest.mark.asyncio
async def test_build_session_id(mock_conv_mgr):
    """build() stores session_id correctly."""
    ctx = await TurnContext.build(
        user_message="msg",
        session_id="my-session-42",
        system_prompt="sp",
        conversation_manager=mock_conv_mgr,
    )
    assert ctx.session_id == "my-session-42"


@pytest.mark.asyncio
async def test_build_handles_call(mock_conv_mgr):
    """build() calls aget_history_messages exactly once."""
    await TurnContext.build(
        user_message="msg",
        session_id="s",
        system_prompt="sp",
        conversation_manager=mock_conv_mgr,
    )
    mock_conv_mgr.aget_history_messages.assert_awaited_once()


# ── Tests for default field values ───────────────────────────────────────────


class TestTurnContextDefaults:
    """Verifies that newly created TurnContext has correct zero values."""

    def test_default_iteration(self):
        ctx = TurnContext()
        assert ctx.iteration == 0

    def test_default_empty_rounds(self):
        ctx = TurnContext()
        assert ctx.empty_rounds == 0

    def test_default_messages(self):
        ctx = TurnContext()
        assert ctx.messages == []

    def test_default_turn_messages(self):
        ctx = TurnContext()
        assert ctx.turn_messages == []

    def test_default_tools(self):
        ctx = TurnContext()
        assert ctx.tools == []

    def test_default_session_id(self):
        ctx = TurnContext()
        assert ctx.session_id == ""

    def test_default_turn_id(self):
        ctx = TurnContext()
        assert ctx.turn_id == ""


# ── Tests for mutability ─────────────────────────────────────────────────────


class TestTurnContextMutability:
    """Verify that fields can be mutated in-place by the agent loop."""

    def test_mutate_iteration(self):
        ctx = TurnContext()
        ctx.iteration = 5
        assert ctx.iteration == 5

    def test_mutate_empty_rounds(self):
        ctx = TurnContext()
        ctx.empty_rounds = 3
        assert ctx.empty_rounds == 3

    def test_mutate_is_finished(self):
        ctx = TurnContext()
        ctx.is_finished = True
        assert ctx.is_finished is True

    def test_mutate_outcome(self):
        ctx = TurnContext()
        ctx.outcome = "final"
        assert ctx.outcome == "final"

    def test_mutate_pending_tool_calls(self):
        ctx = TurnContext()
        tool_call: ParsedToolCall = {
            "id": "c1",
            "name": "find_student",
            "arguments": {"name": "Alice"},
        }
        ctx.pending_tool_calls = [tool_call]
        assert len(ctx.pending_tool_calls) == 1
        assert ctx.pending_tool_calls[0]["name"] == "find_student"

    def test_mutate_messages(self):
        ctx = TurnContext()
        ctx.messages.append({"role": "user", "content": "hello"})
        assert len(ctx.messages) == 1

    def test_mutate_turn_messages(self):
        ctx = TurnContext()
        ctx.turn_messages.append({"role": "assistant", "content": "hi"})
        assert len(ctx.turn_messages) == 1
