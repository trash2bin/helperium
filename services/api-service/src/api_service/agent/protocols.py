"""Narrow interfaces used by the append-only agent loop."""

from __future__ import annotations

from typing import Any, Protocol

from .models import CompletionRequest, CompletionResponse


class LLMProvider(Protocol):
    """A provider is a pure completion transport over a typed request/response."""

    model: str

    async def complete(self, request: CompletionRequest) -> CompletionResponse: ...


class MCPToolResult(Protocol):
    """The minimal result shape the loop needs from a scoped MCP session."""

    ok: bool
    tool_content: str


class MCPToolSession(Protocol):
    """A scoped MCP tool registry and dispatcher."""

    async def list_tools(self) -> list[dict[str, Any]]: ...

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> MCPToolResult: ...


class SpendingPort(Protocol):
    """Post-hoc per-tenant spending accounting.

    This is the always-present accounting surface: cost is recorded after a
    completion returns and the limit check can only stop the next call.
    """

    async def record(self, tenant_id: str, cost: float) -> None: ...

    async def check_limits(self, tenant_id: str) -> tuple[bool, str]: ...


class SpendingReservationPort(Protocol):
    """Two-phase spending admission for one provider completion.

    The loop receives this port only when reservations are explicitly enabled.
    Presence of the port — not attribute sniffing on the accounting tracker —
    is what selects the admission path, so the same code path runs in tests and
    in production.
    """

    async def reserve(
        self,
        principal_id: str,
        request_id: str,
        estimated_cost: float,
        tenant_ids: list[str],
    ) -> Any: ...

    async def commit(self, request_id: str, actual_cost: float) -> None: ...

    async def release(self, request_id: str) -> None: ...


class LoopRecorderPort(Protocol):
    """What the append-only loop itself must be able to record.

    The loop names the event (a completion returned, a tool result arrived);
    where that event goes — durable backlog file, Prometheus series, a future
    sink — is composed outside the domain, in ``agent.adapters``. The field set
    is pinned here on purpose: one call site, one shape, so a new field cannot
    silently reach one consumer and miss another.

    ``duration_ms`` of a completion is the **logical** provider call, measured
    around ``LLMProvider.complete`` and therefore including the provider's
    internal retries and backoff. Measuring a single physical attempt instead
    would attribute retry backoff to the platform.
    """

    def record_llm_call(
        self,
        session_id: str,
        *,
        model: str,
        provider: str,
        duration_ms: float,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        cost: float,
        status: str,
        tenant_ids: list[str],
        turn_id: str,
        iteration: int,
        untrusted_tool_results_in_context: int,
    ) -> None: ...

    def tool_call(
        self,
        session_id: str,
        turn_id: str,
        iteration: int,
        name: str,
        arguments: dict[str, Any],
    ) -> None: ...

    def tool_result(
        self,
        session_id: str,
        turn_id: str,
        iteration: int,
        name: str,
        result: str,
        duration_ms: float,
    ) -> None: ...


class TurnRecorderPort(LoopRecorderPort, Protocol):
    """Turn lifecycle on top of the loop events; used by the orchestrator."""

    def turn_start(self, session_id: str, user_message: str) -> str: ...

    def turn_end(
        self,
        session_id: str,
        *,
        turn_id: str,
        duration_ms: float,
        outcome: str,
        total_prompt_tokens: int = 0,
        total_completion_tokens: int = 0,
        total_cost: float = 0.0,
        llm_calls: int = 0,
        tool_calls: int = 0,
        tool_errors: int = 0,
        empty_results: int = 0,
        empty_rounds: int = 0,
        iterations: int = 0,
        final_length_chars: int = 0,
        final_text: str = "",
        error_message: str = "",
    ) -> None: ...

    def error(
        self,
        session_id: str,
        turn_id: str,
        iteration: int,
        error: str,
        context: dict[str, Any] | None = None,
    ) -> None: ...
