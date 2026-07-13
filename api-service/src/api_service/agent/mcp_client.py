"""MCP (Model Context Protocol) client for tool interaction.

HTTP routes called:
    _open_connection() -> mcp-gateway:GET /mcp (SSE handshake, opens stream)
    call_tool()        -> mcp-gateway:POST /mcp/message?sessionId=... (JSON-RPC)
    list_tools()       -> mcp-gateway:POST /mcp/message?sessionId=... (JSON-RPC)
    _reconnect()       -> mcp-gateway:GET /mcp (SSE reconnection)

MCP transport: legacy HTTP+SSE (GET opens SSE stream, POST sends JSON-RPC).

Talks to the MCP Gateway via the *legacy* HTTP+SSE transport, using the
official `mcp` Python SDK's `sse_client`.

IMPORTANT: this Gateway (see internal/httpclient + main.go) implements the
old two-endpoint SSE pattern, not the newer single-endpoint Streamable HTTP
transport:
  - GET  /mcp (or /sse, /) opens an SSE stream and immediately sends an
    `event: endpoint` with a `messageURL` containing `?sessionId=...`.
  - POST /mcp/message?sessionId=... carries JSON-RPC requests; the Gateway
    replies with a bare 202 Accepted and writes the *actual* JSON-RPC
    response as an `event: message` on the still-open SSE stream from the
    GET request above.

`streamablehttp_client` does NOT speak this dialect (it POSTs JSON-RPC
directly to a single endpoint and expects the response inline). Use
`sse_client` from `mcp.client.sse` instead — it performs the GET-then-POST
handshake and correlates responses arriving on the SSE stream.

Multi-tenancy: `sse_client` accepts a `headers` kwarg. Those headers are
attached to the shared httpx client used for *both* the initial GET (where
the Gateway's sseHandler reads X-Tenant-ID into session.tenantID) and every
subsequent POST (where mcpPostHandler re-reads X-Tenant-ID, falling back to
the value already stored on the session). So passing X-Tenant-ID via
`headers=` covers both code paths on the Go side.

One persistent ClientSession (== one SSE connection == one Gateway session)
is kept per tenant. A lock serializes calls per tenant: the Gateway writes
JSON-RPC responses to a single shared http.ResponseWriter per session, and
concurrent writes to that writer are not safe on the Go side.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.sse import sse_client

from helperium_sdk.settings import settings

logger = logging.getLogger("api_service.agent.mcp_client")

# Timeout for acquiring the per-tenant call lock.
# Kept short so a stuck tool does not block other calls for too long.
LOCK_ACQUIRE_TIMEOUT = 10.0

# Max wall-clock time for a single tool execution after the lock is held.
# Separate from LOCK_ACQUIRE_TIMEOUT so a slow DB query does not starve
# the lock for other callers.
TOOL_EXECUTION_TIMEOUT = 15.0


@dataclass(slots=True)
class ToolResult:
    """Pre-built result of an MCP tool call, ready for LLM consumption.

    Separates the result into tool_content (for role="tool" message)
    and reminder (for preceding role="system" message), ensuring that
    small LLMs (0.5-1.5B) do not ignore the tool result.
    """

    tool_content: str  # Content for role="tool" message
    reminder: str  # System-reminder message for the model
    ok: bool = True
    error: str | None = None


@dataclass(slots=True)
class _TenantConnection:
    """Holds the live streamable-HTTP transport + session for one tenant."""

    tenant_id: str
    session: ClientSession
    http_ctx: Any  # the streamablehttp_client(...) async context manager
    session_ctx: Any  # the ClientSession(...) async context manager
    call_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    list_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    tool_display_names: dict[str, str] = field(default_factory=dict)

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await self.session_ctx.__aexit__(None, None, None)
        with contextlib.suppress(Exception):
            await self.http_ctx.__aexit__(None, None, None)


class MCPClient:
    """Maintains one persistent SSE session per tenant.

    Public API is unchanged from the previous REST-bridge version:
    `get_session()`, `list_tools(session)`, `call_tool(session, name, args)`,
    `close()` — so callers elsewhere in the codebase don't need to change.
    """

    def __init__(self) -> None:
        self._connections: dict[str, _TenantConnection] = {}
        self._registry_lock = asyncio.Lock()

    # -- connection lifecycle -------------------------------------------------

    async def _open_connection(
        self, tenant_ids: list[str] | None = None
    ) -> _TenantConnection:
        """Perform the actual MCP handshake for a tenant or list of tenants.

        When multiple tenant_ids are provided, they are comma-joined into the
        X-Tenant-ID header, which triggers composite mode on mcp-gateway.
        """
        tenant_key = ",".join(tenant_ids) if tenant_ids else ""
        headers = {"X-Tenant-ID": tenant_key} if tenant_key else {}

        logger.info(
            "[MCP] Opening SSE session for tenants=%s", tenant_key or "(default)"
        )
        http_ctx = sse_client(
            settings.mcp_service_url,
            headers=headers,
            timeout=10.0,
            sse_read_timeout=60 * 30,
        )
        try:
            read_stream, write_stream = await http_ctx.__aenter__()
        except Exception:
            logger.exception(
                "[MCP] Failed to open transport for tenants=%s",
                tenant_key or "(default)",
            )
            raise

        session_ctx = ClientSession(read_stream, write_stream)
        try:
            session = await session_ctx.__aenter__()
            await session.initialize()
        except Exception:
            logger.exception(
                "[MCP] Failed to initialize session for tenants=%s",
                tenant_key or "(default)",
            )
            with contextlib.suppress(Exception):
                await http_ctx.__aexit__(None, None, None)
            raise

        logger.info("[MCP] Session ready for tenants=%s", tenant_key or "(default)")
        conn = _TenantConnection(
            tenant_id=tenant_key,
            session=session,
            http_ctx=http_ctx,
            session_ctx=session_ctx,
        )

        # ── Load tool display_name mapping from mcp-gateway ────────────
        try:
            async with httpx.AsyncClient(timeout=5.0) as hclient:
                url = settings.mcp_service_url.rstrip("/") + "/mcp/tools/mapping"
                resp = await hclient.get(url, headers=headers)
                if resp.status_code == 200:
                    conn.tool_display_names = resp.json()
                    logger.info(
                        "[MCP] Loaded %d tool display names for tenants=%s",
                        len(conn.tool_display_names),
                        tenant_key or "(default)",
                    )
        except Exception:
            logger.warning(
                "[MCP] Failed to fetch tool display names for tenants=%s, falling back to tool names",
                tenant_key or "(default)",
            )

        return conn

    async def _get_connection(
        self, tenant_ids: list[str] | None = None
    ) -> _TenantConnection:
        tenant_key = ",".join(tenant_ids) if tenant_ids else ""
        async with self._registry_lock:
            conn = self._connections.get(tenant_key)
            if conn is not None:
                return conn
            conn = await self._open_connection(tenant_ids)
            self._connections[tenant_key] = conn
            return conn

    async def _reconnect(
        self, tenant_ids: list[str] | None = None
    ) -> _TenantConnection:
        tenant_key = ",".join(tenant_ids) if tenant_ids else ""
        async with self._registry_lock:
            old = self._connections.pop(tenant_key, None)
        if old is not None:
            await old.close()
        conn = await self._open_connection(tenant_ids)
        async with self._registry_lock:
            self._connections[tenant_key] = conn
        return conn

    # -- public API -------------------------------------------------------------

    @contextlib.asynccontextmanager
    async def get_session(self, tenant_ids: list[str] | None = None):
        """Async context manager providing a session proxy for specific tenant(s)."""
        proxy = _SessionProxy(self, tenant_ids=tenant_ids or [])
        try:
            yield proxy
        finally:
            pass

    async def list_tools(self, session: "_SessionProxy") -> list[dict[str, Any]]:
        """List available MCP tools for the tenant(s) over the live session.

        Uses its own ``list_lock`` so that ``list_tools`` never blocks
        a concurrent ``call_tool`` and vice versa.
        """
        conn = await self._get_connection(session.tenant_ids)
        try:
            async with asyncio.timeout(LOCK_ACQUIRE_TIMEOUT):
                async with conn.list_lock:
                    result = await conn.session.list_tools()
        except TimeoutError:
            logger.warning(
                "[MCP] list_tools timed out waiting for list lock for tenants=%s",
                session.tenant_ids,
            )
            raise
        except Exception as exc:
            if "Tool not found" in str(exc):
                logger.warning(
                    "[MCP] list_tools encountered Tool not found for tenants=%s, not reconnecting",
                    session.tenant_ids,
                )
                return []

            logger.warning(
                "[MCP] list_tools failed for tenants=%s, reconnecting",
                session.tenant_ids,
            )
            conn = await self._reconnect(session.tenant_ids)
            async with asyncio.timeout(LOCK_ACQUIRE_TIMEOUT):
                async with conn.list_lock:
                    result = await conn.session.list_tools()

        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": tool.inputSchema or {},
                },
            }
            for tool in result.tools
        ]

    async def get_display_name(
        self, tenant_ids: list[str] | None, tool_name: str
    ) -> str | None:
        """Return the user-facing display name for a tool, or None if not available."""
        try:
            conn = await self._get_connection(tenant_ids)
            return conn.tool_display_names.get(tool_name)
        except Exception:
            return None

    async def call_tool(
        self, session: "_SessionProxy", name: str, arguments: dict[str, Any]
    ) -> ToolResult:
        """Call an MCP tool over the live session and return a pre-built ToolResult.

        Preserves the result-processing behaviour of the previous REST-bridge
        version (unwrapping JSON, building reminders) so downstream prompting
        logic doesn't need to change.
        """
        conn = await self._get_connection(session.tenant_ids)
        try:
            async with asyncio.timeout(LOCK_ACQUIRE_TIMEOUT):
                async with conn.call_lock:
                    logger.info(
                        "[MCP] Calling tool %s for tenants=%s with args=%s",
                        name,
                        session.tenant_ids,
                        arguments,
                    )
                    async with asyncio.timeout(TOOL_EXECUTION_TIMEOUT):
                        result = await conn.session.call_tool(name, arguments)
        except TimeoutError:
            logger.warning(
                "[MCP] call_tool %s timed out for tenants=%s (lock=%ss, exec=%ss)",
                name,
                session.tenant_ids,
                LOCK_ACQUIRE_TIMEOUT,
                TOOL_EXECUTION_TIMEOUT,
            )
            return ToolResult(
                tool_content=json.dumps(
                    {
                        "ok": False,
                        "error": f"Tool call timed out (lock timeout {LOCK_ACQUIRE_TIMEOUT}s, exec timeout {TOOL_EXECUTION_TIMEOUT}s)",
                    },
                    ensure_ascii=False,
                ),
                reminder=f"Инструмент {name} не выполнен: таймаут.",
                ok=False,
                error="Tool call timed out",
            )
        except Exception as exc:
            if "Tool not found" in str(exc):
                logger.warning(
                    "[MCP] Tool %s not found for tenants=%s, not reconnecting",
                    name,
                    session.tenant_ids,
                )
                return ToolResult(
                    tool_content=json.dumps(
                        {"ok": False, "error": str(exc)}, ensure_ascii=False
                    ),
                    reminder=f"Инструмент {name} не найден.",
                    ok=False,
                    error=str(exc),
                )
            logger.warning(
                "[MCP] call_tool %s failed for tenants=%s, reconnecting: %s",
                name,
                session.tenant_ids,
                exc,
            )
            try:
                conn = await self._reconnect(session.tenant_ids)
                async with asyncio.timeout(LOCK_ACQUIRE_TIMEOUT):
                    async with conn.call_lock:
                        async with asyncio.timeout(TOOL_EXECUTION_TIMEOUT):
                            result = await conn.session.call_tool(name, arguments)
            except TimeoutError:
                logger.warning(
                    "[MCP] call_tool %s timed out for tenants=%s"
                    " after reconnect (lock=%ss, exec=%ss)",
                    name,
                    session.tenant_ids,
                    LOCK_ACQUIRE_TIMEOUT,
                    TOOL_EXECUTION_TIMEOUT,
                )
                return ToolResult(
                    tool_content=json.dumps(
                        {
                            "ok": False,
                            "error": f"Tool call timed out after reconnect (lock timeout {LOCK_ACQUIRE_TIMEOUT}s, exec timeout {TOOL_EXECUTION_TIMEOUT}s)",
                        },
                        ensure_ascii=False,
                    ),
                    reminder=f"Инструмент {name} не выполнен: таймаут после переподключения.",
                    ok=False,
                    error="Tool call timed out after reconnect",
                )
            except Exception as exc2:
                logger.exception(
                    "[MCP] call_tool %s failed after reconnect, tenants=%s",
                    name,
                    session.tenant_ids,
                )
                return ToolResult(
                    tool_content=json.dumps(
                        {"ok": False, "error": str(exc2)}, ensure_ascii=False
                    ),
                    reminder=f"Инструмент {name} завершился ошибкой.",
                    ok=False,
                    error=str(exc2),
                )

        return self._build_tool_result(name, result)

    # -- result processing ------------------------------------------------------

    @staticmethod
    def _build_tool_result(name: str, result: Any) -> ToolResult:
        """Convert an MCP CallToolResult into the ToolResult shape the rest of
        the codebase expects."""
        text_parts = [
            block.text
            for block in result.content
            if getattr(block, "type", None) == "text"
        ]
        raw_text = "\n".join(text_parts)

        if getattr(result, "isError", False):
            error_text = raw_text or "Unknown error"
            return ToolResult(
                tool_content=json.dumps(
                    {"ok": False, "error": error_text}, ensure_ascii=False
                ),
                reminder=(
                    f"Инструмент {name} вернул ошибку. "
                    "Не повторяй запрос с теми же аргументами."
                ),
                ok=False,
                error=error_text,
            )

        if not raw_text or raw_text in ("null", ""):
            return ToolResult(
                tool_content=json.dumps({"ok": True, "data": None}, ensure_ascii=False),
                reminder=(
                    f"Инструмент {name} вернул пустой результат — "
                    "записи нет, не ищи повторно с теми же аргументами."
                ),
                ok=True,
            )

        # Tools often return JSON-encoded strings; unwrap for a cleaner view.
        try:
            parsed = json.loads(raw_text)
            flat = json.dumps(parsed, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            flat = raw_text

        preview = flat[:200]
        return ToolResult(
            tool_content=flat,
            reminder=(
                f"Инструмент {name} вернул данные: {preview}. "
                "ОБЯЗАТЕЛЬНО покажи эти данные пользователю."
            ),
            ok=True,
        )

    async def close(self) -> None:
        """Close all open tenant sessions."""
        async with self._registry_lock:
            conns = list(self._connections.values())
            self._connections.clear()
        for conn in conns:
            await conn.close()
        logger.info("[MCP] All tenant sessions closed")


class _SessionProxy:
    """Simple proxy that carries the tenant_ids context."""

    def __init__(self, client: MCPClient, tenant_ids: list[str] | None = None) -> None:
        self.client = client
        self.tenant_ids = tenant_ids or []

    async def list_tools(self) -> list[dict[str, Any]]:
        return await self.client.list_tools(self)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        return await self.client.call_tool(self, name, arguments)
