import pytest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from demo.api.agent.orchestrator import LLMAgent


async def async_iter_return_final(final_val):
    yield (None, final_val)


@pytest.mark.asyncio
async def test_llm_agent_stream_events():
    # MagicMock, не AsyncMock — иначе stream_completion() вернёт корутину
    mock_llm_client = MagicMock()
    mock_mcp_client = MagicMock()
    mock_conv_manager = MagicMock()

    # side_effect гарантирует свежий генератор на каждый вызов
    mock_llm_client.stream_completion = MagicMock(
        side_effect=lambda *a, **kw: async_iter_return_final(
            {"role": "assistant", "content": "Hello"}
        )
    )
    mock_llm_client.last_final_message = None  # нужен для fallback-ветки

    # get_session() возвращает async context manager напрямую, не корутину
    mock_mcp_session = AsyncMock()

    @asynccontextmanager
    async def mock_get_session():
        yield mock_mcp_session

    mock_mcp_client.get_session = mock_get_session
    mock_mcp_client.list_tools = AsyncMock(return_value=[])

    # get_history_messages должен вернуть список — он используется через *history
    mock_conv_manager.normalize_session_id.return_value = "default"
    mock_conv_manager.get_history_messages.return_value = []
    # async-обёртки (используются orchestrator)
    mock_conv_manager.aget_history_messages = AsyncMock(return_value=[])
    mock_conv_manager.aremember_turn = AsyncMock(return_value=None)

    with patch("demo.api.agent.orchestrator.backlog"):
        agent = LLMAgent(
            llm_client=mock_llm_client,
            mcp_client=mock_mcp_client,
            conversation_manager=mock_conv_manager,
        )

        events = []
        async for event in agent.stream_events("Hello", session_id="default"):
            events.append(event)

        assert any(e.type == "final" for e in events)
