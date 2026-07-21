"""Protocols (structural subtyping contracts) for the agent module.

Each protocol describes a component boundary that can be satisfied by
multiple implementations (production, mock, alternative provider).

Usage::

    from .protocols import LLMProvider, ConversationStore, MCPToolProvider
"""

from __future__ import annotations

from typing import Protocol, Any, runtime_checkable

from .models import CompletionRequest, CompletionResponse


@runtime_checkable
class LLMProvider(Protocol):
    """Pure LLM invocation boundary.

    Responsible for making the actual LLM call and returning a structured
    response.  Does NOT track cost, emit metrics, or write to the backlog
    — those are the caller's responsibility.

    Implementations: LLMClient (LiteLLM), mock LLMProvider for tests,
    direct API wrappers.
    """

    model: str
    api_base: str | None
    enable_thinking: bool

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        """Execute an LLM completion and return the response.

        This is a plain coroutine (not a streaming generator).  Callers
        that need streaming use the existing ``stream_completion``
        machinery; ``complete()`` is the non-streaming contract.
        """
        ...


class ConversationStore(Protocol):
    """Persistent conversation history storage.

    Implementations: ConversationManager, in-memory store for tests.
    """

    async def aremember_turn(self, session_id: str, messages: Any) -> None:
        """Persist one turn's messages into the session history."""
        ...

    async def aget_history_messages(self, session_id: str) -> list[dict[str, Any]]:
        """Load all prior messages for a session."""
        ...


class SpendingTracker(Protocol):
    """Per-tenant LLM spending tracking and limit enforcement.

    Implementations: SpendingChecker, mock tracker for tests.
    """

    async def record(self, tenant_id: str, cost: float):
        """Record an LLM call cost for a tenant."""
        ...

    async def check_limits(self, tenant_id: str) -> tuple[bool, str]:
        """Check if the tenant has exceeded its spending limit.

        Returns:
            (allowed, reason): ``allowed=True`` means the call can proceed;
            ``allowed=False`` means it should be blocked, along with a
            human-readable reason.
        """
        ...


class BacklogWriter(Protocol):
    """Append-only trace of every model interaction.

    All methods are sync because the underlying ``ModelBacklog`` writes
    to local files synchronously.  Stages call these methods without
    ``await``.

    Implementations: ModelBacklog singleton, ``_AsyncBacklogWriter``,
    ``TestBacklogWriter`` for tests.
    """

    def record_llm_call(self, session_id: str, **kwargs):  # noqa: ANN002, ANN003
        """Record an LLM call with its metadata."""
        ...

    def tool_call(  # noqa: ANN002, ANN003
        self,
        session_id: str,
        turn_id: str,
        iteration: int,
        name: str,
        arguments: dict,
    ):
        """Record a tool call."""
        ...

    def tool_result(  # noqa: ANN002, ANN003
        self,
        session_id: str,
        turn_id: str,
        iteration: int,
        name: str,
        result: str,
        duration_ms: float = 0.0,
    ):
        """Record a tool result."""
        ...

    def error(  # noqa: ANN002, ANN003
        self,
        session_id: str,
        turn_id: str,
        iteration: int,
        error: str,
        context: dict | None = None,
    ):
        """Record an error."""
        ...


class GuardChecker(Protocol):
    """Guard input/output check boundary.

    Implementations: ``GuardChecker`` from ``api_service.guardrails``.
    """

    def check_input(self, message: str) -> Any:
        """Check user message for prompt injection.

        Returns an object with ``.blocked: bool`` and ``.reason: str``.
        """
        ...

    def check_output(self, content: str) -> Any:
        """Check LLM output for leaked system prompt.

        Returns an object with ``.blocked: bool`` and ``.reason: str``.
        """
        ...


@runtime_checkable
class MCPToolProvider(Protocol):
    """MCP tool session and execution boundary.

    Implementations: MCPClient, mock tool provider for tests.
    """

    async def get_session(self, tenant_ids):  # noqa: ANN401
        """Obtain a session scoped to the given tenant IDs."""
        ...

    async def list_tools(self, session) -> list[dict]:  # noqa: ANN401
        """List all available MCP tools for the session."""
        ...

    async def call_tool(  # noqa: ANN401
        self, session, name: str, args: dict[str, Any]
    ) -> Any:
        """Execute a tool call and return the result."""
        ...

    async def get_schema(self, tenant_ids) -> dict | None:  # noqa: ANN401
        """Return the LLM-friendly schema description for the tenant(s)."""
        ...
