"""Test helpers for the agent module.

Provides mock implementations of all protocols and a response builder
for deterministic pipeline testing without a real LLM.

Usage::

    from .helpers import TestLLMProvider, llm_response, TestMCPProvider

    # Queue two LLM responses: first tool_call, then final
    llm = TestLLMProvider()
    llm.queue(
        llm_response.tool_call(name="find_student", args={"name": "Иван"}),
        llm_response.final("Нашёл: Иван Петров, группа ИВТ-21"),
    )

    # Mock MCP returning a record
    mcp = TestMCPProvider()
    mcp.add_tool("find_student", {"ok": True, "data": {"id": "s1", "name": "Иван Петров"}})

    # Run pipeline
    ctx = PipelineContext(turn=turn, llm_provider=llm, mcp_session=mcp, ...)
    async for event in Pipeline(stages=[LLMStage(), ToolExecutionStage()]).run(ctx):
        ...
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from api_service.agent.models import CompletionRequest, CompletionResponse, UsageInfo
from api_service.agent.pipeline import PipelineContext
from api_service.agent.turn_context import TurnContext


# ═══════════════════════════════════════════════════════════════════════════════
# LLM Response Builder — declarative response construction
# ═══════════════════════════════════════════════════════════════════════════════


class llm_response:
    """Factory for ``CompletionResponse`` with fluent construction.

    Examples::

        # Empty / no response
        llm_response.empty()

        # Final content
        llm_response.final("Hello, world!")

        # Tool call (OpenAI-style)
        llm_response.tool_call("get_student", {"id": "s1"})

        # Multiple tool calls
        llm_response.tool_calls([
            ("find_student", {"name": "Иван"}),
            ("get_grades", {"student_id": "s1"}),
        ])

        # Reasoning only
        llm_response.reasoning("Hmm, let me think about this...")

        # Token-by-token streaming
        llm_response.stream(["При", "вет", ",", " мир", "!"])
    """

    @staticmethod
    def empty() -> CompletionResponse:
        """Model returns nothing at all (rare edge case)."""
        return CompletionResponse()

    @staticmethod
    def final(content: str) -> CompletionResponse:
        """Model returns a final answer."""
        return CompletionResponse(
            content=content,
            content_tokens=list(content),
            usage=UsageInfo(
                prompt_tokens=10,
                completion_tokens=len(content),
                total_tokens=10 + len(content),
            ),
            cost=0.001,
        )

    @staticmethod
    def tool_call(
        name: str,
        arguments: dict[str, Any] | None = None,
        reasoning: str | None = None,
    ) -> CompletionResponse:
        """Model returns a single tool call."""
        tc = {
            "id": f"call_{name}_test",
            "type": "function",
            "function": {
                "name": name,
                "arguments": json.dumps(arguments or {}, ensure_ascii=False),
            },
        }
        resp = CompletionResponse(
            tool_calls=[{"id": tc["id"], "name": name, "arguments": arguments or {}}],
            content_tokens=[],
            reasoning_content=reasoning,
            usage=UsageInfo(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            cost=0.001,
        )
        return resp

    @staticmethod
    def tool_calls(
        calls: list[tuple[str, dict[str, Any]]],
        reasoning: str | None = None,
    ) -> CompletionResponse:
        """Model returns multiple tool calls."""
        tool_calls = []
        for name, args in calls:
            tool_calls.append(
                {
                    "id": f"call_{name}_test",
                    "name": name,
                    "arguments": args,
                }
            )
        return CompletionResponse(
            tool_calls=tool_calls,
            content_tokens=[],
            reasoning_content=reasoning,
            usage=UsageInfo(
                prompt_tokens=10,
                completion_tokens=5 * len(calls),
                total_tokens=10 + 5 * len(calls),
            ),
            cost=0.001,
        )

    @staticmethod
    def reasoning(text: str) -> CompletionResponse:
        """Model returns only reasoning (thinking, no tool/content)."""
        return CompletionResponse(
            reasoning_content=text,
            content_tokens=[],
            usage=UsageInfo(prompt_tokens=10, completion_tokens=0, total_tokens=10),
            cost=0.0,
        )

    @staticmethod
    def stream(tokens: list[str]) -> CompletionResponse:
        """Token-by-token streaming.

        The content_tokens will be yielded one by one by the pipeline.
        The content field is the joined result.
        """
        return CompletionResponse(
            content="".join(tokens),
            content_tokens=tokens,
            usage=UsageInfo(
                prompt_tokens=10,
                completion_tokens=len("".join(tokens)),
                total_tokens=10 + len("".join(tokens)),
            ),
            cost=0.001,
        )

    @staticmethod
    def error(message: str = "Simulated LLM error") -> CompletionResponse:
        """If you want to simulate an exception, don't use this — raise in complete() instead.
        This returns an empty response that will be treated as empty_round."""
        return CompletionResponse()


# ═══════════════════════════════════════════════════════════════════════════════
# TestLLMProvider — LLMProvider implementation with queued responses
# ═══════════════════════════════════════════════════════════════════════════════


class TestLLMProvider:
    """LLMProvider that returns pre-built ``CompletionResponse``\\s from a queue.

    Use ``.queue()`` to add responses; each call to ``complete()`` pops one.
    When the queue is empty, raises ``IndexError`` so the test fails fast
    (not hang waiting for a response).

    Example::

        llm = TestLLMProvider()
        llm.queue(
            llm_response.tool_call("find_student", {"name": "Иван"}),
            llm_response.final("Результат: Иван Петров"),
        )

        # First complete() → tool_call
        # Second complete() → final
        # Third complete() → IndexError (test fails)
    """

    def __init__(self, name: str = "test-model") -> None:
        self.model: str = name
        self.api_base: str | None = "http://test"
        self.enable_thinking: bool = False
        self._responses: list[CompletionResponse] = []
        self.call_count: int = 0
        self.call_history: list[CompletionRequest] = []

    def queue(self, *responses: CompletionResponse) -> None:
        """Add one or more responses to the queue (in order)."""
        self._responses.extend(responses)

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        self.call_count += 1
        self.call_history.append(req)
        if not self._responses:
            raise IndexError(
                f"TestLLMProvider.complete() called {self.call_count} times, "
                f"but only {self.call_count - len(self._responses)} responses were queued. "
                "Use .queue() to add more responses."
            )
        return self._responses.pop(0)

    def reset(self) -> None:
        """Clear queue, history, and call count."""
        self._responses.clear()
        self.call_history.clear()
        self.call_count = 0


# ═══════════════════════════════════════════════════════════════════════════════
# TestMCPProvider — MCPToolProvider implementation with hardcoded tools
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class ToolResultData:
    """Simplified tool result for tests (no MCP SDK dependency)."""

    tool_content: str
    ok: bool = True
    error: str | None = None
    reminder: str = ""


class TestMCPProvider:
    """MCPToolProvider with hardcoded tools and results.

    Pre-register tools and their expected results.  Tools not registered
    return an error result.

    Example::

        mcp = TestMCPProvider()
        mcp.add_tool("find_student", {"ok": True, "data": {"name": "Иван"}})
        mcp.add_tool("bad_tool", {"ok": False, "error": "Not found"}, ok=False)
        mcp.set_schema({...})

        session = await mcp.get_session()
        tools = await session.list_tools()
        result = await session.call_tool("find_student", {"name": "Иван"})
    """

    def __init__(self) -> None:
        self._tools: dict[str, dict[str, Any]] = {}
        self._results: dict[str, ToolResultData] = {}
        self._schema: dict | None = None
        self.call_history: list[dict[str, Any]] = []

    def add_tool(
        self,
        name: str,
        result_data: dict[str, Any] | None = None,
        *,
        ok: bool = True,
        description: str = "Test tool",
        input_schema: dict[str, Any] | None = None,
    ) -> None:
        """Register a tool with its expected result.

        Args:
            name: Tool name.
            result_data: JSON-serializable data returned by the tool.
            ok: Whether the tool succeeds.
            description: Tool description for the tool list.
            input_schema: JSON Schema for tool parameters.
        """
        self._tools[name] = {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": input_schema or {"type": "object", "properties": {}},
            },
        }
        if result_data is not None:
            content = json.dumps(result_data, ensure_ascii=False)
            self._results[name] = ToolResultData(
                tool_content=content,
                ok=ok,
                error=None if ok else result_data.get("error", "Unknown error"),
            )

    def set_schema(self, schema: dict | None) -> None:
        """Set the schema returned by get_schema()."""
        self._schema = schema

    def clear(self) -> None:
        """Reset all tools and results."""
        self._tools.clear()
        self._results.clear()
        self._schema = None
        self.call_history.clear()

    # ── MCPToolProvider protocol implementation ─────────────────────────

    async def get_session(self, tenant_ids=None):  # noqa: ANN401
        return _TestSessionProxy(self)

    async def list_tools(self, session) -> list[dict]:  # noqa: ANN401
        return list(self._tools.values())

    async def call_tool(  # noqa: ANN401
        self, session, name: str, arguments: dict[str, Any]
    ) -> Any:
        self.call_history.append({"name": name, "arguments": arguments})
        result = self._results.get(name)
        if result is None:
            return ToolResultData(
                tool_content=json.dumps(
                    {"ok": False, "error": f"Tool '{name}' not found"}
                ),
                ok=False,
                error=f"Tool '{name}' not found",
            )
        return result

    async def get_schema(self, tenant_ids) -> dict | None:  # noqa: ANN401
        return self._schema

    async def get_display_name(self, tenant_ids, tool_name: str) -> str | None:
        return tool_name


class _TestSessionProxy:
    """Thin proxy that delegates back to TestMCPProvider."""

    def __init__(self, provider: TestMCPProvider) -> None:
        self._provider = provider
        self.tenant_ids: list[str] = []

    async def list_tools(self) -> list[dict]:
        return await self._provider.list_tools(self)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        return await self._provider.call_tool(self, name, arguments)

    async def get_schema(self) -> dict | None:
        return await self._provider.get_schema(self.tenant_ids)


# ═══════════════════════════════════════════════════════════════════════════════
# Test conversation / backlog / spending stores
# ═══════════════════════════════════════════════════════════════════════════════


class TestConversationStore:
    """In-memory impl of ConversationStore for tests."""

    def __init__(self) -> None:
        self.history: dict[str, list[dict]] = {}
        self.saved_turns: dict[str, list[list[dict]]] = {}

    async def load_history(self, session_id: str) -> list[dict]:
        return self.history.get(session_id, [])

    async def save_turn(self, session_id: str, messages: list[dict]) -> None:
        if session_id not in self.saved_turns:
            self.saved_turns[session_id] = []
        self.saved_turns[session_id].append(messages)

    # ── Alias used by orchestrator/pipeline ───────────────────────────

    async def aremember_turn(self, session_id: str, messages: list[dict]) -> None:
        await self.save_turn(session_id, messages)


class TestBacklogWriter:
    """In-memory impl of BacklogWriter for tests (sync interface)."""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.turn_ids: dict[str, str] = {}

    def record_llm_call(self, session_id: str, **kwargs: Any) -> None:
        self.events.append({"event": "llm_call", "session_id": session_id, **kwargs})

    def tool_call(
        self,
        session_id: str,
        turn_id: str,
        iteration: int,
        name: str,
        arguments: dict[str, Any],
    ) -> None:
        self.events.append(
            {
                "event": "tool_call",
                "session_id": session_id,
                "turn_id": turn_id,
                "iteration": iteration,
                "name": name,
                "arguments": arguments,
            }
        )

    def tool_result(
        self,
        session_id: str,
        turn_id: str,
        iteration: int,
        name: str,
        result: str,
        duration_ms: float = 0.0,
    ) -> None:
        self.events.append(
            {
                "event": "tool_result",
                "session_id": session_id,
                "turn_id": turn_id,
                "iteration": iteration,
                "name": name,
                "result_chars": len(result),
            }
        )

    def error(
        self,
        session_id: str,
        turn_id: str,
        iteration: int,
        error: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.events.append(
            {
                "event": "error",
                "session_id": session_id,
                "turn_id": turn_id,
                "iteration": iteration,
                "error": error,
                "context": context or {},
            }
        )


class TestSpendingTracker:
    """Allows all spending by default — use ``set_blocked`` to block."""

    def __init__(self) -> None:
        self._records: list[tuple[str, float]] = []
        self._blocked_tenants: set[str] = set()

    def set_blocked(self, tenant_id: str, blocked: bool = True) -> None:
        if blocked:
            self._blocked_tenants.add(tenant_id)
        else:
            self._blocked_tenants.discard(tenant_id)

    async def record(self, tenant_id: str, cost: float) -> None:
        self._records.append((tenant_id, cost))

    async def check_limits(self, tenant_id: str) -> tuple[bool, str]:
        if tenant_id in self._blocked_tenants:
            return (False, f"Spending limit exceeded for {tenant_id}")
        return (True, "")


# ═══════════════════════════════════════════════════════════════════════════════
# PipelineContext builder for tests
# ═══════════════════════════════════════════════════════════════════════════════


async def make_pipeline_ctx(
    *,
    user_message: str = "test",
    session_id: str = "test-session",
    system_prompt: str = "You are a test assistant.",
    tenant_ids: list[str] | None = None,
    conversation_store: TestConversationStore | None = None,
    llm_provider: TestLLMProvider | None = None,
    mcp_provider: TestMCPProvider | None = None,
    max_iterations: int = 5,
    max_empty_rounds: int = 3,
    max_turn_tokens: int = 8000,
    guard_checker: Any = None,
) -> PipelineContext:
    """Build a PipelineContext with test defaults and optional overrides.

    Creates a TurnContext, wires in test stores and providers.
    Uses ``await mcp.get_session()`` so stages can call ``mcp_session.list_tools()``
    without extra arguments (matching production MCPClient._SessionProxy).
    """
    store = conversation_store or TestConversationStore()
    backlog_store = TestBacklogWriter()
    spending_tracker = TestSpendingTracker()
    llm = llm_provider or TestLLMProvider()
    mcp = mcp_provider or TestMCPProvider()

    # Build TurnContext manually (no conversation manager needed)
    turn = TurnContext(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        turn_messages=[{"role": "user", "content": user_message}],
        session_id=session_id,
        turn_id="test-turn-123",
        tenant_ids=tenant_ids or [],
    )

    # Use session proxy (same pattern as production MCPClient)
    mcp_session = await mcp.get_session()

    ctx = PipelineContext(
        turn=turn,
        llm_provider=llm,
        mcp_session=mcp_session,
        store=store,
        spending=spending_tracker,
        backlog=backlog_store,
        max_iterations=max_iterations,
        max_empty_rounds=max_empty_rounds,
        max_turn_tokens=max_turn_tokens,
        guard_checker=guard_checker,
    )
    return ctx


# ═══════════════════════════════════════════════════════════════════════════════
# Collect events helper
# ═══════════════════════════════════════════════════════════════════════════════


async def collect_events(
    gen,
) -> list[tuple[str, Any]]:
    """Safely iterate an async generator and return (type, data) pairs.

    Wraps iteration in try/except so pipeline errors are captured as events.
    """
    events: list[tuple[str, Any]] = []
    async for event in gen:
        events.append((event.type, event.data))
    return events
