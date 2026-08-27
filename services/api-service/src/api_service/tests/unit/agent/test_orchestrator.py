from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest

from api_service.agent.orchestrator import LLMAgent
from api_service.agent.scripted_provider import ScriptedLLMProvider


@dataclass
class _RawResult:
    tool_content: str
    ok: bool = True


class _Conversation:
    def __init__(self) -> None:
        self.saved = []
        self._lock = asyncio.Lock()

    @staticmethod
    def normalize_session_id(session_id):
        return session_id

    async def get_session_lock(self, _session_id):
        return self._lock

    async def aget_history_messages(self, _session_id):
        return []

    async def aremember_turn(self, _session_id, messages):
        self.saved.append(messages)


class _Session:
    def __init__(self) -> None:
        self.calls = []

    async def list_tools(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": "search",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                },
            }
        ]

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "db_map":
            return _RawResult('{"entities":[{"name":"product"}],"workflow_hints":[]}')
        return _RawResult('{"items":["Bosch"]}')


class _MCP:
    def __init__(self) -> None:
        self.tenant_ids = None
        self.disconnect_check = None
        self.session = _Session()

    @asynccontextmanager
    async def get_session(self, tenant_ids, disconnect_check=None):
        self.tenant_ids = tenant_ids
        self.disconnect_check = disconnect_check
        yield self.session


class _GuardResult:
    blocked = False


class _Guard:
    def check_input(self, _text):
        return _GuardResult()

    def check_output(self, _text):
        return _GuardResult()


class _Backlog:
    def __init__(self) -> None:
        self.errors = []

    def turn_start(self, *_args):
        return "turn"

    def error(self, *args, **kwargs):
        self.errors.append((args, kwargs))
        return None

    def turn_end(self, *_args, **_kwargs):
        return None

    def record_llm_call(self, *_args, **_kwargs):
        return None

    def tool_call(self, *_args, **_kwargs):
        return None

    def tool_result(self, *_args, **_kwargs):
        return None


@pytest.mark.asyncio
async def test_orchestrator_uses_one_scoped_append_only_loop(monkeypatch) -> None:
    import api_service.agent.adapters as adapters
    import api_service.agent.orchestrator as orchestrator

    fake_backlog = _Backlog()
    monkeypatch.setattr(orchestrator, "backlog", fake_backlog)
    monkeypatch.setattr(adapters, "backlog", fake_backlog)
    monkeypatch.setattr(orchestrator, "get_guard_checker", lambda: _Guard())

    provider = ScriptedLLMProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call-search",
                        "name": "search",
                        "arguments": {"query": "Bosch"},
                    }
                ]
            },
            {"content": "Found Bosch"},
        ]
    )
    mcp = _MCP()
    conversation = _Conversation()
    agent = LLMAgent(
        llm_client=provider, mcp_client=mcp, conversation_manager=conversation
    )

    events = [
        event
        async for event in agent.stream_events(
            "find Bosch", session_id="s", tenant_ids=["tenant-a"]
        )
    ]

    assert [event.type for event in events] == [
        "tool_call",
        "tool_result",
        "final",
    ]
    assert mcp.tenant_ids == ["tenant-a"]
    assert mcp.session.calls == [
        ("db_map", {}),
        ("search", {"query": "Bosch"}),
    ]
    assert len(provider.requests) == 2
    # Schema preload is injected as a system message before the user turn.
    first_messages = provider.requests[0].messages
    assert first_messages[0]["role"] == "system"
    assert first_messages[1]["role"] == "system"
    assert "Database schema" in first_messages[1]["content"]
    assert [message["role"] for message in conversation.saved[0]] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]


@pytest.mark.asyncio
async def test_orchestrator_records_recovery_exhaustion_in_backlog(monkeypatch) -> None:
    import api_service.agent.adapters as adapters
    import api_service.agent.orchestrator as orchestrator

    fake_backlog = _Backlog()
    monkeypatch.setattr(orchestrator, "backlog", fake_backlog)
    monkeypatch.setattr(adapters, "backlog", fake_backlog)
    monkeypatch.setattr(orchestrator, "get_guard_checker", lambda: _Guard())

    provider = ScriptedLLMProvider(
        [{"tool_calls": [{"id": "call-unknown", "name": "unknown", "arguments": {}}]}]
    )
    mcp = _MCP()
    conversation = _Conversation()
    agent = LLMAgent(
        llm_client=provider, mcp_client=mcp, conversation_manager=conversation
    )

    events = [
        event
        async for event in agent.stream_events(
            "find Bosch", session_id="s", tenant_ids=["tenant-a"]
        )
    ]

    assert [event.type for event in events] == ["tool_call", "tool_result", "error"]
    assert fake_backlog.errors == [
        (
            (
                "s",
                "turn",
                4,
                "Не удалось получить содержательный ответ. Уточните запрос и попробуйте ещё раз.",
            ),
            {"context": {"outcome": "needs_clarification", "retryable": False}},
        )
    ]
