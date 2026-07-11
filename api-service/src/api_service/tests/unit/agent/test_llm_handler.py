"""Tests for LLMHandler — LLM call → outcome resolution."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest

from api_service.agent.llm_handler import LLMHandler
from api_service.agent.turn_context import TurnContext
from api_service.agent.types import AgentEvent


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_stream(
    *tuples: tuple[str | None, dict[str, Any] | None],
) -> AsyncIterator[tuple[str | None, dict[str, Any] | None]]:
    """Create an async generator yielding the given (token, final) tuples."""

    async def gen() -> AsyncIterator[tuple[str | None, dict[str, Any] | None]]:
        for token, final in tuples:
            yield (token, final)

    return gen()


def _make_turn_context(
    messages: list[dict[str, Any]] | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> TurnContext:
    """Create a TurnContext pre-populated for testing."""
    ctx = TurnContext()
    ctx.messages = messages or [
        {"role": "system", "content": "sp"},
        {"role": "user", "content": "hello"},
    ]
    ctx.turn_messages = [{"role": "user", "content": "hello"}]
    ctx.session_id = "test-session"
    ctx.turn_id = "test-turn"
    ctx.tools = tools or []
    return ctx


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_llm():
    """Mock LLMClientProtocol with a replaceable stream_completion.

    stream_completion must be a *sync* callable returning an async iterable,
    because the handler does ``async for ... in llm.stream_completion(...)``
    (no await in front of the call — it's an async generator expression,
    not a coroutine).
    """
    mock = AsyncMock()
    mock.model = "test-model"
    mock.api_base = "http://test"
    mock.enable_thinking = False
    mock.last_final_message = None
    mock.last_usage = None
    mock.last_cost = 0.0
    # Default: empty stream
    mock.stream_completion = lambda messages, tools=None, tenant_ids=None: (
        _make_stream()
    )
    return mock


@pytest.fixture
def handler(mock_llm):
    """LLMHandler wired to the mock LLM."""
    return LLMHandler(llm_client=mock_llm)


# ── Helper: install mock stream ──────────────────────────────────────────────


def _install_stream(mock_llm, *tuples):
    """Replace stream_completion with one returning given (token, final) tuples.

    Creates a *fresh* generator on every call so multiple invocations
    work correctly.
    """
    mock_llm.stream_completion = lambda messages, tools=None, tenant_ids=None: (
        _make_stream(*tuples)
    )


# ── Test scenarios ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestLLMHandler:
    """All LLMHandler test scenarios."""

    async def test_tool_calls_outcome_state(self, mock_llm, handler):
        """When LLM returns tool_calls, outcome="tool_calls" and pending_tool_calls populated."""
        _install_stream(
            mock_llm,
            (
                None,
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "find_student",
                                "arguments": '{"name":"Alice"}',
                            },
                        }
                    ],
                },
            ),
        )

        ctx = _make_turn_context()
        async for _ in handler.stream_and_parse(ctx):
            pass

        assert ctx.outcome == "tool_calls"
        assert len(ctx.pending_tool_calls) == 1
        assert ctx.pending_tool_calls[0]["name"] == "find_student"
        assert ctx.pending_tool_calls[0]["arguments"] == {"name": "Alice"}
        assert ctx.is_finished is False
        # Assistant message with tool_calls appended to messages
        assert len(ctx.messages) == 3
        assert ctx.messages[2]["role"] == "assistant"
        assert "tool_calls" in ctx.messages[2]

    async def test_final_outcome(self, mock_llm, handler):
        """When LLM returns final content, outcome="final" and is_finished=True."""
        _install_stream(
            mock_llm,
            ("Hel", None),
            ("lo!", None),
            (None, {"role": "assistant", "content": "Hello!"}),
        )

        ctx = _make_turn_context()
        events: list[AgentEvent] = [e async for e in handler.stream_and_parse(ctx)]

        assert len(events) == 2
        assert events[0].type == "token"
        assert events[0].data["data"] == "Hel"
        assert events[1].type == "token"
        assert events[1].data["data"] == "lo!"

        assert ctx.outcome == "final"
        assert ctx.is_finished is True
        assert ctx.turn_messages[-1]["role"] == "assistant"
        assert ctx.turn_messages[-1]["content"] == "Hello!"

    async def test_empty_outcome(self, mock_llm, handler):
        """When LLM returns nothing, outcome="empty_round"."""
        _install_stream(mock_llm)
        mock_llm.last_final_message = None

        ctx = _make_turn_context()
        ctx.empty_rounds = 0
        events = [e async for e in handler.stream_and_parse(ctx)]

        assert len(events) == 0
        assert ctx.outcome == "empty_round"
        assert ctx.empty_rounds == 1

    async def test_empty_outcome_with_last_final(self, mock_llm, handler):
        """When stream empty but last_final_message exists, use that."""
        _install_stream(mock_llm)
        mock_llm.last_final_message = {
            "role": "assistant",
            "content": "cached answer",
        }

        ctx = _make_turn_context()
        async for _ in handler.stream_and_parse(ctx):
            pass

        assert ctx.outcome == "final"
        assert ctx.is_finished is True
        assert ctx.turn_messages[-1]["content"] == "cached answer"

    async def test_partial_reasoning_outcome(self, mock_llm, handler):
        """reasoning_content without content/tool_calls → empty_round with reasoning."""
        _install_stream(
            mock_llm,
            (
                None,
                {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "thinking step 1",
                },
            ),
        )

        ctx = _make_turn_context()
        ctx.empty_rounds = 0
        async for _ in handler.stream_and_parse(ctx):
            pass

        assert ctx.outcome == "empty_round"
        assert ctx.empty_rounds == 1
        assert any(m.get("content") == "thinking step 1" for m in ctx.messages)
        from api_service.agent.prompts import PARTIAL_REMINDER

        assert any(m.get("content") == PARTIAL_REMINDER for m in ctx.messages)

    async def test_partial_no_reasoning(self, mock_llm, handler):
        """Empty content, no reasoning → empty_round."""
        _install_stream(
            mock_llm,
            (None, {"role": "assistant", "content": ""}),
        )

        ctx = _make_turn_context()
        ctx.empty_rounds = 0
        async for _ in handler.stream_and_parse(ctx):
            pass

        assert ctx.outcome == "empty_round"
        assert ctx.empty_rounds == 1

    async def test_stream_completion_called_with_correct_args(self, mock_llm, handler):
        """stream_completion receives ctx.messages and ctx.tools."""
        captured_args = {}

        def capture(messages, tools=None, tenant_ids=None):
            captured_args["messages"] = messages
            captured_args["tools"] = tools
            return _make_stream((None, {"role": "assistant", "content": "ok"}))

        mock_llm.stream_completion = capture

        ctx = _make_turn_context()
        ctx.tools = [{"function": {"name": "test_tool"}}]
        async for _ in handler.stream_and_parse(ctx):
            pass

        assert captured_args["messages"] == ctx.messages
        assert captured_args["tools"] == ctx.tools

    async def test_stream_completion_no_tools(self, mock_llm, handler):
        """When tools list is empty, pass None as tools."""
        captured_args = {}

        def capture(messages, tools=None, tenant_ids=None):
            captured_args["messages"] = messages
            captured_args["tools"] = tools
            return _make_stream((None, {"role": "assistant", "content": "ok"}))

        mock_llm.stream_completion = capture

        ctx = _make_turn_context()
        ctx.tools = []
        async for _ in handler.stream_and_parse(ctx):
            pass

        assert captured_args["tools"] is None

    async def test_increments_empty_rounds(self, mock_llm, handler):
        """Multiple empty rounds increment empty_rounds."""
        _install_stream(mock_llm)
        mock_llm.last_final_message = None

        ctx = _make_turn_context()
        ctx.empty_rounds = 2

        async for _ in handler.stream_and_parse(ctx):
            pass

        assert ctx.empty_rounds == 3

    async def test_multiple_tool_calls_parsed(self, mock_llm, handler):
        """Response with multiple tool_calls extracts all of them."""
        _install_stream(
            mock_llm,
            (
                None,
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "tool_a", "arguments": '{"x":1}'},
                        },
                        {
                            "id": "c2",
                            "type": "function",
                            "function": {"name": "tool_b", "arguments": '{"y":2}'},
                        },
                    ],
                },
            ),
        )

        ctx = _make_turn_context()
        async for _ in handler.stream_and_parse(ctx):
            pass

        assert ctx.outcome == "tool_calls"
        assert len(ctx.pending_tool_calls) == 2
        assert ctx.pending_tool_calls[0]["name"] == "tool_a"
        assert ctx.pending_tool_calls[1]["name"] == "tool_b"

    async def test_resets_outcome_before_each_call(self, mock_llm, handler):
        """outcome and pending_tool_calls reset before each call."""
        _install_stream(
            mock_llm,
            (
                None,
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "tool_a", "arguments": "{}"},
                        }
                    ],
                },
            ),
        )
        ctx = _make_turn_context()
        async for _ in handler.stream_and_parse(ctx):
            pass
        assert ctx.outcome == "tool_calls"

        # Second call — different outcome
        _install_stream(
            mock_llm,
            (None, {"role": "assistant", "content": "final answer"}),
        )
        async for _ in handler.stream_and_parse(ctx):
            pass
        assert ctx.outcome == "final"
        assert len(ctx.pending_tool_calls) == 0

    async def test_tool_calls_yields_no_token_events(self, mock_llm, handler):
        """Tool calls outcome yields no token events."""
        _install_stream(
            mock_llm,
            (
                None,
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "test_tool", "arguments": "{}"},
                        }
                    ],
                },
            ),
        )

        ctx = _make_turn_context()
        events = [e async for e in handler.stream_and_parse(ctx)]
        assert len(events) == 0
