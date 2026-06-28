"""MCP (Model Context Protocol) client for tool interaction.

Долгоживущая MCP-сессия с автоматическим переподключением при сбоях.
Не создаёт новое HTTP-соединение на каждый tool call — это дорого по latency.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from demo.settings import settings

logger = logging.getLogger("demo.api.agent.mcp_client")


class MCPClient:
    """Handles MCP session lifecycle and tool interactions.

    Хранит одно долгоживущее соединение к MCP-серверу, переиспользует
    между последовательными turn-ами одного процесса. При сбое —
    ленивое переподключение на следующем вызове.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._session: ClientSession | None = None
        self._session_id: str | None = None
        self._streams_cm: Any = None  # streamable_http_client context manager
        self._session_cm: Any = None  # ClientSession context manager

    async def _ensure_session(self) -> ClientSession:
        """Ленивое создание/пересоздание MCP-сессии."""
        async with self._lock:
            if self._session is not None:
                return self._session

            logger.info("[MCP] Opening HTTP session to %s", settings.mcp_service_url)
            self._streams_cm = streamable_http_client(
                url=settings.mcp_service_url,
                terminate_on_close=True,
            )
            read, write, get_session_id = await self._streams_cm.__aenter__()
            self._session_cm = ClientSession(read, write)
            self._session = await self._session_cm.__aenter__()
            await self._session.initialize()
            try:
                self._session_id = get_session_id() if callable(get_session_id) else get_session_id
            except Exception:
                self._session_id = None
            logger.info("[MCP] Session ready (id=%s)", self._session_id)
            return self._session

    async def close(self) -> None:
        """Закрыть текущую сессию (если есть). Безопасно вызывать несколько раз.

        Детачит state под локом, а закрытие контекстных менеджеров делает без лока —
        чтобы __aexit__ из anyio (cancel scope) не падал, если вызывается из другой задачи.
        """
        async with self._lock:
            if self._session is None:
                return
            logger.info("[MCP] Closing session")
            session_cm = self._session_cm
            streams_cm = self._streams_cm
            self._session = None
            self._session_cm = None
            self._streams_cm = None
            self._session_id = None

        # Закрываем контекстные менеджеры без лока — они привязаны к задаче,
        # которая создала сессию, и могут не совпадать с текущей.
        for name, cm in [("session", session_cm), ("streams", streams_cm)]:
            if cm is not None:
                try:
                    await cm.__aexit__(None, None, None)
                except RuntimeError as exc:
                    if "cancel scope" in str(exc).lower():
                        logger.debug("[MCP] Cancel scope mismatch on %s (session dead)", name)
                    else:
                        logger.warning("[MCP] Error closing %s: %s", name, exc)
                except Exception as exc:
                    logger.warning("[MCP] Error closing %s: %s", name, exc)

    @asynccontextmanager
    async def get_session(self):
        """Async context manager: возвращает активную сессию, переподключает при сбое.

        Использование:
            async with mcp_client.get_session() as session:
                await session.call_tool(...)
        """
        session = await self._ensure_session()
        try:
            yield session
        except Exception as exc:
            # Любая ошибка в работе с сессией → закрываем, чтобы следующий вызов пересоздал
            logger.warning("[MCP] Session error, will reconnect on next call: %s", exc)
            await self.close()
            raise

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
                return json.dumps({"ok": True, "data": structured}, ensure_ascii=False)

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