"""MCP (Model Context Protocol) client for tool interaction."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from demo.settings import PROJECT_ROOT, settings

logger = logging.getLogger("demo.api.agent.mcp_client")


class MCPClient:
    """Handles MCP session lifecycle and tool interactions."""

    def __init__(self) -> None:
        self._session_locks: dict[str, Any] = {}

    @asynccontextmanager
    async def get_session(self) -> AsyncIterator[ClientSession]:
        """Context manager for MCP session."""
        logger.debug("[MCP] Creating session...")
        server_path = str(PROJECT_ROOT / "server.py")
        params = StdioServerParameters(
            command=settings.python_executable,
            args=[server_path],
            env={**dict(os.environ), "PYTHONPATH": str(PROJECT_ROOT)},
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                logger.debug("[MCP] Session initialized")
                yield session
                logger.debug("[MCP] Session closing")

    async def list_tools(self, session: ClientSession) -> list[dict[str, Any]]:
        """List available MCP tools."""
        result = await session.list_tools()
        tools: list[dict[str, Any]] = []
        for tool in result.tools:
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description or "",
                        "parameters": tool.inputSchema,
                    },
                }
            )
        return tools

    async def call_tool(
        self, session: ClientSession, name: str, arguments: dict[str, Any]
    ) -> str:
        """Call an MCP tool and return JSON result."""
        try:
            logger.debug("[MCP] Calling tool: %s with args: %s", name, arguments)
            result = await session.call_tool(name, arguments)

            if result.isError:
                error_text = self._collect_text_content(result)
                return json.dumps(
                    {"ok": False, "error": error_text or f"Error calling tool {name}"},
                    ensure_ascii=False,
                )

            structured = getattr(result, "structuredContent", None)
            if structured is not None:
                return json.dumps(
                    {"ok": True, "data": structured}, ensure_ascii=False
                )

            content = self._collect_text_content(result)
            return json.dumps({"ok": True, "data": content}, ensure_ascii=False)

        except Exception as exc:
            logger.exception("[MCP] Exception calling tool %s", name)
            return json.dumps(
                {"ok": False, "error": f"Error calling {name}: {exc}"},
                ensure_ascii=False,
            )

    @staticmethod
    def _collect_text_content(result: Any) -> str:
        """Extract text content from MCP result."""
        return "\n".join(
            getattr(item, "text", "")
            for item in getattr(result, "content", []) or []
            if getattr(item, "text", None)
        )
