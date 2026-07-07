"""Tests for FallbackHandler — graceful degradation on empty answer."""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest

from api_service.agent.fallback_handler import FallbackHandler
from api_service.agent.prompts import FALLBACK_GENERIC
from api_service.agent.turn_context import TurnContext
from api_service.agent.types import AgentEvent


# ── Helpers ──────────────────────────────────────────────────────────────────


def _token_stream(*tokens: str) -> AsyncIterator[str]:
    """Async generator yielding string tokens."""

    async def gen() -> AsyncIterator[str]:
        for t in tokens:
            yield t

    return gen()


def _make_turn_context() -> TurnContext:
    """Create a populated TurnContext."""
    ctx = TurnContext()
    ctx.messages = [
        {"role": "system", "content": "sp"},
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "q3"},
    ]
    ctx.turn_messages = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "q3"},
    ]
    ctx.session_id = "test-session"
    return ctx


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_llm():
    """Mock LLMClientProtocol with configurable get_final_message.

    get_final_message must be a sync function (not async) that returns
    an async iterator, because the handler does::

        async for token in self._llm.get_final_message(fallback_messages):

    and async generators don't need await — they return async iterators
    directly when called.
    """
    mock = AsyncMock()
    mock.model = "test-model"
    mock.api_base = "http://test"
    mock.enable_thinking = False
    # By default: empty stream
    mock.get_final_message = lambda msgs: _token_stream()
    return mock


@pytest.fixture
def mock_conv_mgr():
    """Mock ConversationManager."""
    mgr = AsyncMock()
    mgr.aget_history_messages.return_value = []
    return mgr


@pytest.fixture
def handler(mock_llm, mock_conv_mgr):
    """FallbackHandler wired to mocks."""
    return FallbackHandler(llm_client=mock_llm, conversation_manager=mock_conv_mgr)


# ── Tests ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestFallbackHandler:
    """All FallbackHandler scenarios."""

    async def test_has_tokens(self, mock_llm, mock_conv_mgr, handler):
        """get_final_message yields tokens → token events + final event."""
        mock_llm.get_final_message = lambda msgs: _token_stream("Hello", " ", "world")

        ctx = _make_turn_context()
        initial_msg_count = len(ctx.messages)

        events: list[AgentEvent] = [
            e async for e in handler.run(ctx, was_finished=False)
        ]

        # Token events for each yielded token + final event
        assert len(events) == 4
        assert events[0].type == "token"
        assert events[0].data["data"] == "Hello"
        assert events[1].type == "token"
        assert events[1].data["data"] == " "
        assert events[2].type == "token"
        assert events[2].data["data"] == "world"
        assert events[3].type == "final"
        assert events[3].data["content"] == "Hello world"

        # Assistant message appended to turn_messages
        assert ctx.turn_messages[-1]["role"] == "assistant"
        assert ctx.turn_messages[-1]["content"] == "Hello world"

        # Messages should NOT have been modified (trimmed copy sent to LLM)
        assert len(ctx.messages) == initial_msg_count

        # aremember_turn was called
        mock_conv_mgr.aremember_turn.assert_awaited_once()

    async def test_empty_not_finished(self, mock_llm, handler):
        """get_final_message yields nothing and was_finished=False → FALLBACK_GENERIC."""
        mock_llm.get_final_message = lambda msgs: _token_stream()  # empty

        ctx = _make_turn_context()
        events = [e async for e in handler.run(ctx, was_finished=False)]

        # Should have 2 events: generic token + final
        assert len(events) == 2
        assert events[0].type == "token"
        assert events[0].data["data"] == FALLBACK_GENERIC
        assert events[1].type == "final"
        assert events[1].data["content"] == FALLBACK_GENERIC

    async def test_empty_was_finished(self, mock_llm, handler):
        """get_final_message yields nothing and was_finished=True → no generic, final with ''."""
        mock_llm.get_final_message = lambda msgs: _token_stream()

        ctx = _make_turn_context()
        events = [e async for e in handler.run(ctx, was_finished=True)]

        # Should have 1 event: final with empty string
        assert len(events) == 1
        assert events[0].type == "final"
        assert events[0].data["content"] == ""

    async def test_trim_for_fallback_applied(self, mock_llm, handler):
        """get_final_message receives trimmed messages (system + last 4)."""
        captured: list[list] = []

        def capture(msgs):
            captured.append(msgs)
            return _token_stream("ok")

        mock_llm.get_final_message = capture

        ctx = _make_turn_context()  # 6 messages
        async for _ in handler.run(ctx, was_finished=False):
            pass

        assert len(captured) == 1
        call_messages = captured[0]

        # First should be system prompt
        assert call_messages[0]["role"] == "system"
        # Total = system + last 4 = 5
        assert len(call_messages) == 5
        # The last 4 should match the original last 4
        assert call_messages[1:] == ctx.messages[-4:]

    async def test_trim_called_for_small_context(self, mock_llm, handler):
        """With 3 or fewer messages, trim returns them all."""
        captured: list[list] = []

        def capture(msgs):
            captured.append(msgs)
            return _token_stream("ok")

        mock_llm.get_final_message = capture

        ctx = TurnContext()
        ctx.messages = [
            {"role": "system", "content": "sp"},
            {"role": "user", "content": "hi"},
        ]
        ctx.turn_messages = [{"role": "user", "content": "hi"}]
        ctx.session_id = "s"

        async for _ in handler.run(ctx, was_finished=False):
            pass

        assert len(captured) == 1
        assert len(captured[0]) == 2  # unchanged

    async def test_aremember_turn_called_correctly(
        self, mock_llm, mock_conv_mgr, handler
    ):
        """aremember_turn receives the right session_id and turn_messages."""
        mock_llm.get_final_message = lambda msgs: _token_stream("Hello world")

        ctx = _make_turn_context()
        async for _ in handler.run(ctx, was_finished=False):
            pass

        mock_conv_mgr.aremember_turn.assert_awaited_once_with(
            ctx.session_id,
            ctx.turn_messages,
        )

    async def test_real_stream_of_tokens(self, mock_llm, handler):
        """Multiple token events yield the correct final text."""
        mock_llm.get_final_message = lambda msgs: _token_stream("a", "b", "c")

        ctx = _make_turn_context()
        parts: list[str] = []
        async for event in handler.run(ctx, was_finished=False):
            if event.type == "token":
                parts.append(event.data["data"])

        assert "".join(parts) == "abc"
