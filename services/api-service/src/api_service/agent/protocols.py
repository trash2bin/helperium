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
