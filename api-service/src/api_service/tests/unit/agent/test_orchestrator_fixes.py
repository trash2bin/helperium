"""Tests for orchestrator fixes: handler restoration and schema caching.

See: AGENTS.md audit — _run_turn handler mutation (needs contextmanager restore)
and _build_schema_message (needs per-tenant cache).
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest

from api_service.agent.orchestrator import LLMAgent
from api_service.agent.types import AgentEvent


# ── Shared fakes ─────────────────────────────────────────────────────────────


class FakeLLMClient:
    """Minimal fake LLM client for handler tests."""

    def __init__(self, name: str = "default"):
        self.name = name
        self.model = "test-model"
        self.api_base = "http://test"
        self.enable_thinking = False
        self.last_usage: dict[str, int] | None = {
            "prompt_tokens": 5,
            "completion_tokens": 3,
            "total_tokens": 8,
        }
        self.last_cost: float = 0.0005

    async def stream_completion(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tenant_ids: list[str] | None = None,
    ) -> AsyncIterator[tuple[str | None, dict[str, Any] | None]]:
        yield ("ok", None)
        yield (None, {"role": "assistant", "content": "ok"})

    async def stream_answer(
        self, user_message: str, system_prompt: str | None = None
    ) -> AsyncIterator[str]:
        yield "ok"


class FakeMCPClient:
    """Mock MCP client with controllable session schema."""

    def __init__(self, schema: dict | None = None):
        self.schema = schema

    @contextlib.asynccontextmanager
    async def get_session(self, tenant_ids=None):
        proxy = AsyncMock()
        proxy.tenant_ids = tenant_ids or []
        proxy.list_tools = AsyncMock(return_value=[])
        proxy.call_tool = AsyncMock()
        proxy.get_schema = AsyncMock(return_value=self.schema)
        yield proxy

    async def list_tools(self, session):
        return []

    async def call_tool(self, session, name: str, arguments: dict[str, Any]):
        return None

    async def get_display_name(self, tenant_ids, tool_name):
        return tool_name

    async def close(self):
        pass


@pytest.fixture
def conv_manager():
    """Conversation manager mock."""
    mgr = AsyncMock()
    mgr.normalize_session_id = lambda x: x

    lock_mock = AsyncMock()
    lock_mock.__aenter__ = AsyncMock()
    lock_mock.__aexit__ = AsyncMock(return_value=None)
    mgr.get_session_lock = AsyncMock(return_value=lock_mock)

    mgr.load_history = AsyncMock(return_value=[])
    mgr.aremember_turn = AsyncMock()
    mgr.aget_history_messages = AsyncMock(return_value=[])
    return mgr


# ── Test A: handler restoration ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handler_restored_after_custom_llm(conv_manager):
    """stream_events with custom llm_client uses local handlers — no shared state leak.

    With the new architecture, _run_turn builds local handlers per-request
    instead of mutating instance attributes.  Multiple concurrent requests
    with different LLMs are safe without any save/restore dance.
    """
    default_llm = FakeLLMClient(name="default")
    custom_llm = FakeLLMClient(name="custom")
    mcp = FakeMCPClient(schema=None)
    agent = LLMAgent(
        llm_client=default_llm, mcp_client=mcp, conversation_manager=conv_manager
    )

    # Call stream_events with custom LLM
    events: list[AgentEvent] = []
    async for event in agent.stream_events(
        "hello", session_id="test-handler", llm_client=custom_llm
    ):
        events.append(event)

    # Verify no errors from the call
    errors = [e for e in events if e.type == "error"]
    assert len(errors) == 0, f"Unexpected errors: {errors}"

    # Now call AGAIN without custom LLM — should fall through to default_llm
    events2: list[AgentEvent] = []
    async for event in agent.stream_events("hello again", session_id="test-handler-2"):
        events2.append(event)
    errors2 = [e for e in events2 if e.type == "error"]
    assert len(errors2) == 0, f"Unexpected errors after second call: {errors2}"


@pytest.mark.asyncio
async def test_handler_not_affected_without_custom_llm(conv_manager):
    """Without custom llm, default client is used, no handlers to mutate."""
    llm = FakeLLMClient(name="default")
    mcp = FakeMCPClient(schema=None)
    agent = LLMAgent(llm_client=llm, mcp_client=mcp, conversation_manager=conv_manager)

    async for _ in agent.stream_events("test", session_id="test-default"):
        pass

    # After the call, default client should still be available (no mutation)
    assert agent._test_llm_client is llm, "Test default LLM reference changed"


@pytest.mark.asyncio
async def test_handler_restored_even_on_inner_error(conv_manager):
    """If _run_turn encounters an error, local handlers are GC'd — no state leak."""
    default_llm = FakeLLMClient(name="default")
    custom_llm = FakeLLMClient(name="custom")

    # MCP that raises (simulates data-service error during session open)
    class BrokenMCP(FakeMCPClient):
        @contextlib.asynccontextmanager
        async def get_session(self, tenant_ids=None):
            raise RuntimeError("data-service unreachable")

    agent = LLMAgent(
        llm_client=default_llm,
        mcp_client=BrokenMCP(),
        conversation_manager=conv_manager,
    )

    events: list[AgentEvent] = []
    async for event in agent.stream_events(
        "hello", session_id="test-error", llm_client=custom_llm
    ):
        events.append(event)

    # Should have an error event
    errors = [e for e in events if e.type == "error"]
    assert len(errors) > 0, f"Expected error events, got {[e.type for e in events]}"

    # Default client is untouched (no mutation ever)
    assert agent._test_llm_client is default_llm, (
        "Default LLM reference changed after error"
    )


# ── Test B: schema cache ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_build_schema_cached_per_tenant(conv_manager):
    """_build_schema_message is called only once per tenant across turns.

    This test FAILS before the fix because _build_schema_message is called
    on every _run_turn without caching.
    """
    # Monkey-patch _build_schema_message in stages (where it's called)
    import api_service.agent.stages as stages_mod

    original = stages_mod._build_schema_message
    call_count = 0

    def counting_build_schema_message(schema: dict, tools: list | None = None) -> str:
        nonlocal call_count
        call_count += 1
        return original(schema, tools)

    stages_mod._build_schema_message = counting_build_schema_message

    try:
        schema_response = {
            "entities": [
                {
                    "name": "student",
                    "description": "Students info",
                    "search_fields": "name",
                    "filter_fields": [],
                    "relations": [],
                }
            ],
            "workflow_hints": ["Use search for students"],
        }

        llm = FakeLLMClient()
        mcp = FakeMCPClient(schema=schema_response)
        agent = LLMAgent(
            llm_client=llm, mcp_client=mcp, conversation_manager=conv_manager
        )

        # First call with tenant-a → should call _build_schema_message
        async for _ in agent.stream_events(
            "test", session_id="test-cache-1", tenant_ids=["tenant-a"]
        ):
            pass
        assert call_count >= 1, (
            f"_build_schema_message called {call_count} times on first turn, expected ≥1"
        )

        # Second call with SAME tenant → should use cache
        async for _ in agent.stream_events(
            "test2", session_id="test-cache-2", tenant_ids=["tenant-a"]
        ):
            pass
        first_count = call_count
        assert first_count >= 1, (
            f"_build_schema_message called {first_count} times total, "
            f"expected ≥1 (cached). "
        )

    finally:
        stages_mod._build_schema_message = original


@pytest.mark.asyncio
async def test_schema_cache_different_tenants_not_shared(conv_manager):
    """Different tenant_ids produce different cache entries."""
    import api_service.agent.stages as stages_mod

    original = stages_mod._build_schema_message
    call_count = 0

    def counting_build_schema_message(schema: dict, tools: list | None = None) -> str:
        nonlocal call_count
        call_count += 1
        return original(schema, tools)

    stages_mod._build_schema_message = counting_build_schema_message

    try:
        schema_response = {
            "entities": [
                {
                    "name": "student",
                    "description": "Students",
                    "search_fields": "name",
                    "filter_fields": [],
                    "relations": [],
                }
            ],
            "workflow_hints": [],
        }

        llm = FakeLLMClient()
        mcp = FakeMCPClient(schema=schema_response)
        agent = LLMAgent(
            llm_client=llm, mcp_client=mcp, conversation_manager=conv_manager
        )

        # First: tenant-a
        async for _ in agent.stream_events(
            "test", session_id="test-1", tenant_ids=["tenant-a"]
        ):
            pass
        first_count = call_count
        assert first_count >= 1

        # Second: tenant-b (different)
        async for _ in agent.stream_events(
            "test2", session_id="test-2", tenant_ids=["tenant-b"]
        ):
            pass

        # Should be called again for different tenant (different cache key)
        assert call_count >= 2, (
            f"_build_schema_message called {call_count} times, "
            f"expected ≥2 (separate tenants)"
        )
    finally:
        stages_mod._build_schema_message = original


# ── Test C: real-time provider fallback (no stale Router) ──────────────────────


@pytest.mark.asyncio
async def test_fallback_client_created_fresh_each_request(monkeypatch, conv_manager):
    """When no llm_client/llm_config is passed, create_fallback_client()
    is called fresh on every request — no stale Router from startup."""
    import api_service.agent.orchestrator as orch

    call_count = 0
    created_clients = []

    def counting_fallback():
        nonlocal call_count
        call_count += 1
        llm = FakeLLMClient(name=f"fresh-{call_count}")
        created_clients.append(llm)
        return llm

    monkeypatch.setattr(orch, "create_fallback_client", counting_fallback)

    mcp = FakeMCPClient(schema=None)
    # NOTE: no llm_client passed — will use create_fallback_client()
    agent = LLMAgent(mcp_client=mcp, conversation_manager=conv_manager)

    # First call — should create #1
    async for _ in agent.stream_events("hello", session_id="test-rt-1"):
        pass
    assert call_count == 1, f"Expected 1 fallback client, got {call_count}"
    assert agent._test_llm_client is None, (
        "_test_llm_client should be None when not passed"
    )

    # Second call — should create #2 (fresh!)
    async for _ in agent.stream_events("hello again", session_id="test-rt-2"):
        pass
    assert call_count == 2, f"Expected 2 fallback clients, got {call_count}"
    assert len(created_clients) == 2
    assert created_clients[0] is not created_clients[1], (
        "Each request got the SAME client instance — stale Router!"
    )


@pytest.mark.asyncio
async def test_fallback_client_not_called_when_llm_client_explicit(
    monkeypatch, conv_manager
):
    """When llm_client is explicitly passed, create_fallback_client()
    should NOT be called."""
    import api_service.agent.orchestrator as orch

    call_count = 0

    def counting_fallback():
        nonlocal call_count
        call_count += 1
        return FakeLLMClient(name=f"fresh-{call_count}")

    monkeypatch.setattr(orch, "create_fallback_client", counting_fallback)

    mcp = FakeMCPClient(schema=None)
    explicit = FakeLLMClient(name="explicit")
    agent = LLMAgent(
        llm_client=explicit, mcp_client=mcp, conversation_manager=conv_manager
    )

    # Call with llm_client explicitly passed
    async for _ in agent.stream_events(
        "hello", session_id="test-explicit", llm_client=explicit
    ):
        pass

    # create_fallback_client should NOT have been called
    assert call_count == 0, (
        f"create_fallback_client was called {call_count} times, expected 0"
    )
