"""TDD test: история сессии должна сохраняться даже при исключении в pipeline.

Проблема: LLMAgent.stream_events() ловит исключение через except/finally,
НО не вызывает SaveHistoryStage.force_save(). В результате turn_messages
(сообщение пользователя + любые промежуточные данные) теряются.

Текущее поведение (баг): при исключении aremember_turn() НЕ вызывается.
Ожидаемое поведение (фикс): даже при исключении turn должен быть сохранён.

Тест ПАДАЕТ пока фикс не внедрён — это TDD-контракт.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api_service.agent.orchestrator import LLMAgent, backlog
from api_service.agent.conversation import ConversationManager
from api_service.agent.mcp_client import MCPClient


class ExplodingLLM:
    """LLM провайдер, который всегда выбрасывает исключение.

    Используется для симуляции ошибки внутри Pipeline.run().
    """

    model: str = "test-exploding-model"
    api_base: str | None = "http://test"
    enable_thinking: bool = False

    async def complete(self, req):
        raise RuntimeError("Simulated LLM failure inside pipeline")


def _make_conv_mgr():
    """Build a mock ConversationManager that tracks aremember_turn calls."""
    conv_mgr = MagicMock(spec=ConversationManager)
    conv_mgr.normalize_session_id.return_value = "test-session"
    conv_mgr.get_session_lock.return_value = asyncio.Lock()
    conv_mgr.aget_history_messages = AsyncMock(return_value=[])
    conv_mgr.aremember_turn = AsyncMock()
    return conv_mgr


def _make_mcp_session():
    """Build a working MCP session mock."""
    mcp = AsyncMock(spec=MCPClient)
    session = AsyncMock()
    session.list_tools.return_value = []
    session.call_tool.return_value = None
    session.get_schema.return_value = None
    session.get_display_name.return_value = None
    session.__aenter__.return_value = session
    session.__aexit__.return_value = None
    mcp.get_session.return_value = session
    return mcp


class TestHistorySavedOnError:
    """История сессии сохраняется при любом исключении в pipeline."""

    @pytest.mark.asyncio
    async def test_history_saved_when_llm_crashes(self):
        """При падении LLM → aremember_turn() ДОЛЖЕН быть вызван.

        Сейчас (баг): except в orchestrator.py отдаёт error event,
        finally пишет backlog, но SaveHistoryStage не запускается.
        turn_messages теряются, сессия "забывает" этот turn.

        TDD: этот тест падает (AssertionError) пока фикс не внедрён.
        """
        conv_mgr = _make_conv_mgr()
        mcp_client = _make_mcp_session()

        with patch.object(backlog, "turn_start", return_value="test-turn-id"):
            agent = LLMAgent(
                llm_client=ExplodingLLM(),
                mcp_client=mcp_client,
                conversation_manager=conv_mgr,
            )

            events: list = []
            async for event in agent.stream_events(
                user_message="тестовое сообщение",
                session_id="test-session",
                lang="ru",
            ):
                events.append(event)

        error_events = [e for e in events if e.type == "error"]
        assert len(error_events) >= 1, (
            f"Должен быть хотя бы 1 error event при падении LLM. "
            f"Все события: {[(e.type, str(e.data)[:60]) for e in events]}"
        )

        # ⚡ TDD-контракт: aremember_turn ДОЛЖЕН был быть вызван
        assert conv_mgr.aremember_turn.called, (
            "\n\n❌ TDD FAIL: aremember_turn() НЕ БЫЛ ВЫЗВАН при исключении.\n"
            "История сессии потеряна. Фикс: в except/finally блоке"
            " orchestrator.py нужно вызвать SaveHistoryStage().force_save(pipeline_ctx).\n"
            "Пока этот тест падает — баг не исправлен.\n\n"
            f"conversation_manager mock calls: {conv_mgr.method_calls}"
        )

    @pytest.mark.asyncio
    async def test_history_saved_when_mcp_connection_fails(self):
        """При ошибке открытия MCP сессии → история ДОЛЖНА сохраниться.

        MCPClient.get_session() выбрасывает исключение — это ещё раньше,
        чем pipeline.run(). Но turn_messages с user_message должны быть
        сохранены даже без pipeline.
        """
        conv_mgr = _make_conv_mgr()
        mcp_client = AsyncMock(spec=MCPClient)
        mcp_client.get_session.side_effect = ConnectionError(
            "Cannot connect to mcp-gateway:8083"
        )

        with patch.object(backlog, "turn_start", return_value="test-turn-id"):
            agent = LLMAgent(
                llm_client=ExplodingLLM(),
                mcp_client=mcp_client,
                conversation_manager=conv_mgr,
            )

            events: list = []
            async for event in agent.stream_events(
                user_message="тест MCP ошибка",
                session_id="test-session",
                lang="ru",
            ):
                events.append(event)

        error_events = [e for e in events if e.type == "error"]
        assert len(error_events) >= 1

        # ⚡ TDD-контракт: даже при MCP ошибке история должна быть
        assert conv_mgr.aremember_turn.called, (
            "\n\n❌ TDD FAIL: aremember_turn() НЕ ВЫЗВАН при ошибке MCP.\n"
            "Даже если MCP сессия не открылась — user_message уже есть в "
            "turn_messages и должен быть сохранён.\n"
            f"Mock calls: {conv_mgr.method_calls}"
        )

    @pytest.mark.asyncio
    async def test_turn_messages_preserved_in_history(self):
        """После исключения в pipeline сохранённые turn_messages содержат
        сообщение пользователя."""
        conv_mgr = _make_conv_mgr()
        mcp_client = _make_mcp_session()

        with patch.object(backlog, "turn_start", return_value="test-turn-id"):
            agent = LLMAgent(
                llm_client=ExplodingLLM(),
                mcp_client=mcp_client,
                conversation_manager=conv_mgr,
            )

            events: list = []
            async for event in agent.stream_events(
                user_message="важное сообщение от пользователя",
                session_id="test-session",
                lang="ru",
            ):
                events.append(event)

        # ⚡ TDD-контракт: turn_messages содержат user_message
        if conv_mgr.aremember_turn.called:
            call_args = conv_mgr.aremember_turn.call_args
            saved_messages = call_args[0][1] if call_args else []
            user_contents = [
                m.get("content", "") for m in saved_messages if m.get("role") == "user"
            ]
            assert len(user_contents) >= 1, (
                "Хотя бы одно user сообщение должно быть сохранено"
            )
        else:
            pytest.fail(
                "TDD: aremember_turn() не вызван. "
                "turn_messages не сохранены в истории сессии."
            )
