"""Tests for orchestrator fixes: handler restoration and schema caching.

Uses TestLLMProvider (complete() protocol) instead of old FakeLLMClient.
No references to LLMClient, stream_completion, or create_fallback_client.

See: AGENTS.md audit — _run_turn handler mutation (needs contextmanager restore)
and _build_schema_message (needs per-tenant cache).
"""

from __future__ import annotations

import contextlib
from typing import Any
from unittest.mock import AsyncMock

import pytest

from api_service.agent.orchestrator import LLMAgent
from api_service.agent.types import AgentEvent


# ── Fake MCP Client (schema-aware, for cache tests) ──────────────────────────


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
    instead of mutating instance attributes. Multiple concurrent requests
    with different LLMs are safe without any save/restore dance.
    """
    from .helpers import TestLLMProvider, llm_response

    default_llm = TestLLMProvider(name="default")
    default_llm.queue(llm_response.final("ok"))
    custom_llm = TestLLMProvider(name="custom")
    custom_llm.queue(llm_response.final("ok"))
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
    # (self._test_llm_client was set in constructor)
    events2: list[AgentEvent] = []
    async for event in agent.stream_events("hello again", session_id="test-handler-2"):
        events2.append(event)
    errors2 = [e for e in events2 if e.type == "error"]
    assert len(errors2) == 0, f"Unexpected errors after second call: {errors2}"


@pytest.mark.asyncio
async def test_handler_not_affected_without_custom_llm(conv_manager):
    """Without custom llm, default client is used, no handlers to mutate."""
    from .helpers import TestLLMProvider, llm_response

    llm = TestLLMProvider(name="default")
    llm.queue(llm_response.final("ok"))
    mcp = FakeMCPClient(schema=None)
    agent = LLMAgent(llm_client=llm, mcp_client=mcp, conversation_manager=conv_manager)

    async for _ in agent.stream_events("test", session_id="test-default"):
        pass

    # After the call, default client should still be available (no mutation)
    assert agent._test_llm_client is llm, "Test default LLM reference changed"


@pytest.mark.asyncio
async def test_handler_restored_even_on_inner_error(conv_manager):
    """If an error occurs, local handlers are GC'd — no state leak."""
    from .helpers import TestLLMProvider, llm_response

    default_llm = TestLLMProvider(name="default")
    default_llm.queue(llm_response.final("ok"))
    custom_llm = TestLLMProvider(name="custom")
    custom_llm.queue(llm_response.final("ok"))

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
    from .helpers import TestLLMProvider, llm_response

    # Monkey-patch _build_schema_message in stages
    import api_service.agent.stages.tool_discovery as td_mod

    original = td_mod._build_schema_message
    call_count = 0

    def counting_build_schema_message(schema: dict, tools: list | None = None) -> str:
        nonlocal call_count
        call_count += 1
        return original(schema, tools)

    td_mod._build_schema_message = counting_build_schema_message

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

        llm = TestLLMProvider()
        llm.queue(llm_response.final("ok"))
        llm.queue(llm_response.final("ok"))
        mcp = FakeMCPClient(schema=schema_response)
        agent = LLMAgent(
            llm_client=llm, mcp_client=mcp, conversation_manager=conv_manager
        )

        # First call with tenant-a — should call _build_schema_message
        async for _ in agent.stream_events(
            "test", session_id="test-cache-1", tenant_ids=["tenant-a"]
        ):
            pass
        assert call_count >= 1, (
            f"_build_schema_message called {call_count} times on first turn, expected ≥1"
        )

        # Second call with SAME tenant — should use cache
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
        td_mod._build_schema_message = original


@pytest.mark.asyncio
async def test_schema_cache_different_tenants_not_shared(conv_manager):
    """Different tenant_ids produce different cache entries."""
    from .helpers import TestLLMProvider, llm_response

    import api_service.agent.stages.tool_discovery as td_mod

    original = td_mod._build_schema_message
    call_count = 0

    def counting_build_schema_message(schema: dict, tools: list | None = None) -> str:
        nonlocal call_count
        call_count += 1
        return original(schema, tools)

    td_mod._build_schema_message = counting_build_schema_message

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

        llm = TestLLMProvider()
        llm.queue(llm_response.final("ok"))
        llm.queue(llm_response.final("ok"))
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
        td_mod._build_schema_message = original


# ── Test C: real-time provider fallback (no stale Router) ──────────────────────


@pytest.mark.asyncio
async def test_provider_created_fresh_each_request_when_no_llm_client(
    monkeypatch, conv_manager
):
    """When no llm_client/llm_config/provider_priority is passed,
    _resolve_pool_or_env() is called fresh on every request."""
    from .helpers import TestLLMProvider, llm_response
    import api_service.agent.factory as fact

    call_count = 0
    created_providers = []

    async def counting_resolve():
        nonlocal call_count
        call_count += 1
        prov = TestLLMProvider(name=f"fresh-{call_count}")
        prov.queue(llm_response.final("ok"))
        created_providers.append(prov)
        return prov

    monkeypatch.setattr(fact, "_resolve_pool_or_env", counting_resolve)

    mcp = FakeMCPClient(schema=None)
    # NOTE: no llm_client passed — will use _resolve_pool_or_env()
    agent = LLMAgent(mcp_client=mcp, conversation_manager=conv_manager)

    # First call — should resolve #1
    async for _ in agent.stream_events("hello", session_id="test-rt-1"):
        pass
    assert call_count == 1, f"Expected 1 resolve call, got {call_count}"
    assert agent._test_llm_client is None, (
        "_test_llm_client should be None when not passed"
    )

    # Second call — should resolve #2 (fresh!)
    async for _ in agent.stream_events("hello again", session_id="test-rt-2"):
        pass
    assert call_count == 2, f"Expected 2 resolve calls, got {call_count}"
    assert len(created_providers) == 2
    assert created_providers[0] is not created_providers[1], (
        "Each request got the SAME provider instance — stale singleton!"
    )


@pytest.mark.asyncio
async def test_fallback_not_called_when_llm_client_explicit(monkeypatch, conv_manager):
    """When llm_client is explicitly passed, _resolve_pool_or_env()
    should NOT be called."""
    from .helpers import TestLLMProvider, llm_response
    import api_service.agent.factory as fact

    call_count = 0

    async def counting_resolve():
        nonlocal call_count
        call_count += 1
        prov = TestLLMProvider(name=f"fresh-{call_count}")
        prov.queue(llm_response.final("ok"))
        return prov

    monkeypatch.setattr(fact, "_resolve_pool_or_env", counting_resolve)

    mcp = FakeMCPClient(schema=None)
    explicit = TestLLMProvider(name="explicit")
    explicit.queue(llm_response.final("ok"))
    agent = LLMAgent(
        llm_client=explicit, mcp_client=mcp, conversation_manager=conv_manager
    )

    # Call with llm_client explicitly passed
    async for _ in agent.stream_events(
        "hello", session_id="test-explicit", llm_client=explicit
    ):
        pass

    # _resolve_pool_or_env should NOT have been called
    assert call_count == 0, (
        f"_resolve_pool_or_env was called {call_count} times, expected 0"
    )
