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
        # Task, в которой был вызван __aenter__ на стримах.
        # MCP streamable_http_client использует anyio task_group, привязанный
        # к task создания; закрытие __aexit__ из чужой task падает с
        # "Attempted to exit cancel scope in a different task".
        self._owner_task: asyncio.Task[Any] | None = None
        # True пока идёт фоновое закрытие — защита от двойного close().
        self._closing = False

    async def _ensure_session(self) -> ClientSession:
        """Ленивое создание/пересоздание MCP-сессии."""
        async with self._lock:
            if self._session is not None:
                return self._session

            logger.info("[MCP] Opening HTTP session to %s", settings.mcp_service_url)
            # Запоминаем task создания: close() должен корректно сработать
            # даже если вызван из другой task (lifespan shutdown vs. request handler).
            self._owner_task = asyncio.current_task()
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

        Если вызов пришёл из task, отличной от owner_task (типичный случай —
        lifespan shutdown, когда сессию открывал request handler), корректное
        закрытие планируется в фоне через asyncio.create_task: новая task не
        привязана к чужой cancel-scope цепочке, поэтому anyio внутри
        streamable_http_client не падает с "cancel scope" и DELETE-запрос
        к MCP-серверу действительно отправляется (terminate_on_close=True).
        """
        async with self._lock:
            if self._session is None or self._closing:
                return
            self._closing = True
            logger.info("[MCP] Closing session")
            session_cm = self._session_cm
            streams_cm = self._streams_cm
            owner_task = self._owner_task
            self._session = None
            self._session_cm = None
            self._streams_cm = None
            self._session_id = None
            self._owner_task = None

        # Если мы в чужой task — закрываем в фоне. Это и есть фикс cancel-scope.
        if owner_task is not None and asyncio.current_task() is not owner_task:
            logger.debug(
                "[MCP] close() called from a different task; scheduling background cleanup"
            )
            try:
                loop = asyncio.get_running_loop()
                task = loop.create_task(
                    self._close_context_managers(session_cm, streams_cm)
                )
                # Сохраняем ссылку, чтобы task не была собрана GC раньше времени.
                self._background_close_task = task
            except RuntimeError as exc:
                logger.warning(
                    "[MCP] Could not schedule background close (loop unavailable): %s", exc
                )
            return

        await self._close_context_managers(session_cm, streams_cm)

    async def _close_context_managers(
        self,
        session_cm: Any,
        streams_cm: Any,
    ) -> None:
        """Фактическое закрытие ClientSession и streamable_http_client.

        Вызывается в task, которая либо совпадает с owner_task, либо
        создана заново через create_task — в обоих случаях __aexit__
        anyio task_group завершается корректно и terminate_session()
        отправляет DELETE на MCP-сервер.
        """
        for name, cm in [("session", session_cm), ("streams", streams_cm)]:
            if cm is None:
                continue
            try:
                await cm.__aexit__(None, None, None)
            except Exception as exc:  # noqa: BLE001
                # Любая ошибка здесь означает, что terminate_session() мог не
                # отработать; логируем как warning, не как success.
                logger.warning("[MCP] Error closing %s: %s", name, exc)
        self._closing = False

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