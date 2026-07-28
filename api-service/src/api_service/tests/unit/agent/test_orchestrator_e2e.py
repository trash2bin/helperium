"""E2E tests for LLMAgent: tests with protocol-based providers (no legacy LLMClient).

All tests use TestLLMProvider (complete() protocol) instead of old
FakeLLMClient (stream_completion()).  Legacy adapter tests removed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from api_service.agent.orchestrator import LLMAgent
from api_service.agent.types import AgentEvent


# ── Conversation Manager mock ─────────────────────────────────────────────────


@pytest.fixture
def conv_manager():
    """Creates a mock ConversationManager."""
    mgr = MagicMock()
    mgr.normalize_session_id = MagicMock(side_effect=lambda x: x)

    lock_mock = AsyncMock()
    lock_mock.__aenter__ = AsyncMock()
    lock_mock.__aexit__ = AsyncMock(return_value=None)
    mgr.get_session_lock = AsyncMock(return_value=lock_mock)

    mgr.load_history = AsyncMock(return_value=[])
    mgr.aremember_turn = AsyncMock()
    mgr.aget_history_messages = AsyncMock(return_value=[])
    return mgr


# ── New-style orchestrator tests (LLMProvider protocol, not old LLMClient) ──


class TestLLMAgentWithProtocolProvider:
    """LLMAgent.stream_events() c TestLLMProvider (protocol-based, not legacy adapter)."""

    @pytest.mark.asyncio
    async def test_tool_call_then_final(self, conv_manager):
        """Tool call (LAYER 1) -> result -> final."""
        from .helpers import TestLLMProvider, TestMCPProvider, llm_response

        llm = TestLLMProvider()
        llm.queue(
            llm_response.tool_call("test_tool", {"key": "value"}),
            llm_response.final("Вот данные!"),
        )
        mcp = TestMCPProvider()
        mcp.add_tool(
            "test_tool", {"id": "s1", "name": "Alice"}, description="Test tool"
        )

        agent = LLMAgent(
            llm_client=llm, mcp_client=mcp, conversation_manager=conv_manager
        )

        events: list[AgentEvent] = []
        async for event in agent.stream_events(
            "найди данные", session_id="test-protocol"
        ):
            events.append(event)

        event_types = [e.type for e in events]

        assert "tool_call" in event_types, f"Ожидался tool_call: {event_types}"
        assert "tool_result" in event_types, f"Ожидался tool_result: {event_types}"
        assert "final" in event_types, f"Ожидался final: {event_types}"

        # Проверяем что tool_call данные есть
        tool_call_events = [e for e in events if e.type == "tool_call"]
        assert tool_call_events[0].data.get("name") == "test_tool"

        # Проверяем что финал содержит данные
        final_events = [e for e in events if e.type == "final"]
        final_content = ""
        for ev in final_events:
            if isinstance(ev.data, dict):
                final_content = ev.data.get("content", "")
        assert "Alice" in final_content or "данные" in final_content, (
            f"Финал не содержит данных: {final_content[:200]}"
        )

        # Нет ошибок
        errors = [e for e in events if e.type == "error"]
        assert len(errors) == 0, f"Неожиданные ошибки: {errors}"

    @pytest.mark.asyncio
    async def test_text_tool_calls(self, conv_manager):
        """Tool calls как JSON в content (LAYER 2) -> result -> final."""
        from .helpers import TestLLMProvider, TestMCPProvider, llm_response

        llm = TestLLMProvider()
        llm.queue(
            llm_response.text_tool_calls([("test_tool", {"key": "value"})]),
            llm_response.final("Готово!"),
        )
        mcp = TestMCPProvider()
        mcp.add_tool("test_tool", {"id": "s1", "name": "Alice"})

        agent = LLMAgent(
            llm_client=llm, mcp_client=mcp, conversation_manager=conv_manager
        )

        events: list[AgentEvent] = []
        async for event in agent.stream_events(
            "найди данные", session_id="test-text-tools"
        ):
            events.append(event)

        event_types = [e.type for e in events]
        assert "tool_call" in event_types, f"Текст-тул не сработал: {event_types}"
        assert "tool_result" in event_types
        assert "final" in event_types
