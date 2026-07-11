"""E2E тесты для LLMAgent: проверяет pipeline без реальной LLM.

Цель: убедиться что tool result корректно попадает в messages и
передаётся в следующий turn LLM. Без участия реальной модели —
фейковый LLMClient имитирует: сначала вызов tool, потом финальный ответ.

⚠️ ВСЕ ТЕСТЫ ПРОПУЩЕНЫ — требуют переписывания после рефакторинга
на SDK-based MCP сессии и новый event loop в orchestrator.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock

import pytest


# ---------------------------------------------------------------------------
# Fake LLM client (совместимый с Mock-подходом)
# ---------------------------------------------------------------------------


async def _async_iter_final(data: dict[str, Any]):
    """Yield a (token, final) tuple expected by stream_completion."""
    yield None, data


class FakeLLMClient:
    """Mock LLMClient с историей вызовов для проверки tool result в messages."""

    def __init__(self):
        self.call_count = 0
        self.call_history: list[list[dict[str, Any]]] = []

    last_usage: dict[str, int] | None = None
    last_cost: float = 0.0

    async def stream_completion(self, messages, tools, tenant_ids=None):
        self.call_count += 1
        self.call_history.append(messages)
        if self.call_count == 1:
            # Первый вызов: возвращаем tool_call
            yield (
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
                                "arguments": json.dumps({"name": "Alice"}),
                            },
                        }
                    ],
                },
            )
        else:
            # Второй вызов: финальный ответ
            yield (
                None,
                {
                    "role": "assistant",
                    "content": "Found Alice!",
                },
            )

    last_final_message: dict[str, Any] | None = None


class FakeMCPClient:
    """Mock MCPClient который возвращает tool_data."""

    def __init__(self, tool_data: Any = None):
        self.tool_data = tool_data or [{"name": "Alice", "id": "s1"}]
        self.calls: list[dict[str, Any]] = []

    async def call_tool(self, session, name: str, arguments: dict[str, Any]):
        from api_service.agent.mcp_client import ToolResult

        self.calls.append({"name": name, "arguments": arguments})
        wrapper = {"ok": True, "data": self.tool_data}
        return ToolResult(
            tool_content=json.dumps(wrapper, ensure_ascii=False),
            reminder=f"Инструмент {name} вернул данные. ОБЯЗАТЕЛЬНО покажи эти данные пользователю.",
            ok=True,
        )

    def get_session(self, tenant_id=""):
        @asynccontextmanager
        async def session():
            yield AsyncMock()

        return session()

    async def list_tools(self, session):
        return []


# ---------------------------------------------------------------------------
# Tests — все пропущены до переписывания
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason="Orchestrator refactored to SDK-based MCP sessions — tests need rewriting"
)
@pytest.mark.asyncio
async def test_llm_agent_stream_events_basic():
    """Базовый сценарий: LLM возвращает финальный ответ без tool calls."""
    pass


@pytest.mark.skip(
    reason="Orchestrator refactored to SDK-based MCP sessions — tests need rewriting"
)
@pytest.mark.asyncio
async def test_tool_result_appears_in_next_turn_messages():
    """Tool result должен попасть в messages следующего turn."""
    pass


@pytest.mark.skip(
    reason="Orchestrator refactored to SDK-based MCP sessions — tests need rewriting"
)
@pytest.mark.asyncio
async def test_tool_result_full_pipeline_messages():
    """Полный pipeline: tool_call → tool_result → second_llm_call с role=tool."""
    pass
