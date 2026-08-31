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
