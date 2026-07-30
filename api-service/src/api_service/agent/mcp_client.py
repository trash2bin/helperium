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
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.sse import sse_client

from helperium_sdk.settings import settings

logger = logging.getLogger("api_service.agent.mcp_client")

# All MCP client constants are managed via env vars through settings.
# See helperium_sdk.settings.DemoSettings for the full list:
#   MCP_MAX_CONSECUTIVE_FAILURES  (default: 3)
#   MCP_CIRCUIT_BREAKER_TIMEOUT   (default: 30.0)
#   MCP_GC_INTERVAL               (default: 60.0)
#   MCP_MAX_IDLE_SECONDS          (default: 600.0)
#   MCP_LOCK_ACQUIRE_TIMEOUT      (default: 10.0)
#   MCP_TOOL_EXECUTION_TIMEOUT    (default: 15.0)
#   MCP_SSE_TIMEOUT               (default: 10.0)
#   MCP_SSE_READ_TIMEOUT          (default: 1800.0)
#   MCP_SESSION_INIT_TIMEOUT      (default: 15.0)


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
    schema: dict | None = None  # LLM-friendly schema description (from /mcp/schema)
    last_used: float = field(default_factory=time.monotonic)
    # Circuit breaker state
    consecutive_failures: int = 0
    last_failure_time: float = 0.0

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
        # GC background task
        self._gc_task: asyncio.Task | None = None
        self._gc_started: bool = False

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
            timeout=settings.mcp_sse_timeout,
            sse_read_timeout=settings.mcp_sse_read_timeout,
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
            async with asyncio.timeout(settings.mcp_session_init_timeout):
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

        # ── Load LLM-friendly schema from mcp-gateway ──────────────────
        try:
            async with httpx.AsyncClient(timeout=5.0) as hclient:
                url = settings.mcp_service_url.rstrip("/") + "/mcp/schema"
                resp = await hclient.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    conn.schema = data
                    entities = len(data.get("entities", []))
                    hints = len(data.get("workflow_hints", []))
                    logger.info(
                        "[MCP] Loaded schema for tenants=%s: %d entities, %d hints",
                        tenant_key or "(default)",
                        entities,
                        hints,
                    )
        except Exception:
            logger.warning(
                "[MCP] Failed to fetch schema for tenants=%s — LLM will not have schema context",
                tenant_key or "(default)",
            )

        return conn

    def _is_circuit_open(self, conn: _TenantConnection) -> bool:
        """Check if circuit breaker is open for this connection.

        Open means: consecutive_failures >= MAX and haven't waited long enough.
        """
        if conn.consecutive_failures < settings.mcp_max_consecutive_failures:
            return False
        elapsed = time.monotonic() - conn.last_failure_time
        return elapsed < settings.mcp_circuit_breaker_timeout

    def _mark_success(self, conn: _TenantConnection) -> None:
        """Reset circuit breaker on successful operation."""
        conn.consecutive_failures = 0
        conn.last_failure_time = 0.0

    def _mark_failure(self, conn: _TenantConnection) -> None:
        """Increment circuit breaker on failure."""
        conn.consecutive_failures += 1
        conn.last_failure_time = time.monotonic()

    async def _get_connection(
        self, tenant_ids: list[str] | None = None
    ) -> _TenantConnection:
        tenant_key = ",".join(tenant_ids) if tenant_ids else ""
        async with self._registry_lock:
            conn = self._connections.get(tenant_key)
            if conn is not None:
                # Circuit breaker: if open, raise immediately
                if self._is_circuit_open(conn):
                    logger.warning(
                        "[MCP] Circuit breaker open for tenants=%s "
                        "(%d failures, %.1fs ago), skipping reconnect",
                        tenant_key or "(default)",
                        conn.consecutive_failures,
                        time.monotonic() - conn.last_failure_time,
                    )
                    raise ConnectionError(
                        f"Circuit breaker open for {tenant_key or '(default)'}: "
                        f"{conn.consecutive_failures} consecutive failures, "
                        f"retry in {settings.mcp_circuit_breaker_timeout - (time.monotonic() - conn.last_failure_time):.0f}s"
                    )

                # Session idle > 4 min — reconnect proactively
                idle = time.monotonic() - conn.last_used
                if idle > 240:
                    logger.info(
                        "[MCP] Session for tenants=%s idle %.0fs, reconnecting",
                        tenant_key or "(default)",
                        idle,
                    )
                    old = self._connections.pop(tenant_key, None)
                    if old is not None:
                        await old.close()
                    conn = await self._open_connection(tenant_ids)
                    self._connections[tenant_key] = conn
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
            # Carry over circuit breaker state from old connection
            old_failures = old.consecutive_failures
            await old.close()
        else:
            old_failures = 0

        try:
            conn = await self._open_connection(tenant_ids)
            # Reset circuit breaker on successful reconnect
            conn.consecutive_failures = 0
            conn.last_failure_time = 0.0
        except Exception:
            # Reconnect failed — increment circuit breaker
            logger.warning(
                "[MCP] Reconnect failed for tenants=%s (accumulated %d failures)",
                tenant_key or "(default)",
                old_failures + 1,
            )
            # Blow the circuit — mark as failed and re-raise
            async with self._registry_lock:
                existing = self._connections.get(tenant_key)
                if existing is not None:
                    existing.consecutive_failures = old_failures + 1
                    existing.last_failure_time = time.monotonic()
            raise

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
            async with asyncio.timeout(settings.mcp_lock_acquire_timeout):
                async with conn.list_lock:
                    result = await conn.session.list_tools()
                    conn.last_used = time.monotonic()
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
            async with asyncio.timeout(settings.mcp_lock_acquire_timeout):
                async with conn.list_lock:
                    result = await conn.session.list_tools()
                    conn.last_used = time.monotonic()

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

    async def get_schema(self, tenant_ids: list[str] | None = None) -> dict | None:
        """Return the LLM-friendly schema description for tenant(s).

        Schema is fetched once when the MCP connection opens and cached
        on the connection object. Returns None if unavailable.
        """
        try:
            conn = await self._get_connection(tenant_ids)
            return conn.schema
        except Exception:
            return None

    # -- TTL Garbage Collection (MEDIUM-3 fix) ------------------------------

    async def _cleanup_stale_connections(
        self, max_idle_seconds: float = settings.mcp_max_idle_seconds
    ) -> None:
        """Close and remove connections idle longer than max_idle_seconds.

        Idempotent: errors from individual close() are logged but don't
        prevent cleanup of other connections.

        Safe to call from outside the registry lock; acquires internally.
        """
        stale: list[tuple[str, _TenantConnection]] = []
        async with self._registry_lock:
            now = time.monotonic()
            for key, conn in list(self._connections.items()):
                idle = now - conn.last_used
                if idle > max_idle_seconds:
                    stale.append((key, conn))
                    del self._connections[key]

        if not stale:
            logger.debug("[MCP_GC] No stale connections found")
            return

        for key, conn in stale:
            try:
                await conn.close()
                logger.info(
                    "[MCP_GC] Closed idle session for tenants=%s (idle > %.0fs)",
                    key or "(default)",
                    max_idle_seconds,
                )
            except Exception:
                logger.warning(
                    "[MCP_GC] Failed to close idle session for tenants=%s",
                    key or "(default)",
                )

        logger.info("[MCP_GC] Cleaned %d stale connection(s)", len(stale))

    async def start_gc(
        self, interval_seconds: float = settings.mcp_gc_interval
    ) -> None:
        """Start a background GC task that periodically closes idle sessions.

        Idempotent: subsequent calls are no-ops.
        """
        if self._gc_started:
            return
        self._gc_started = True

        async def _gc_loop():
            while True:
                try:
                    await asyncio.sleep(interval_seconds)
                    await self._cleanup_stale_connections()
                except asyncio.CancelledError:
                    break
                except Exception:
                    logger.exception("[MCP_GC] GC loop error")

        self._gc_task = asyncio.create_task(_gc_loop())
        self._gc_task.set_name("mcp_client_gc")
        logger.info(
            "[MCP_GC] Background GC started (interval=%ds, max_idle=%ds)",
            interval_seconds,
            settings.mcp_max_idle_seconds,
        )

    async def stop_gc(self) -> None:
        """Cancel the background GC task."""
        self._gc_started = False
        if self._gc_task and not self._gc_task.done():
            self._gc_task.cancel()
            try:
                await self._gc_task
            except asyncio.CancelledError:
                pass
            self._gc_task = None
            logger.info("[MCP_GC] Background GC stopped")

    # -- tool call execution helpers -----------------------------------------

    @staticmethod
    def _timeout_error_result(
        name: str, tenant_ids: list[str], *, after_reconnect: bool = False
    ) -> ToolResult:
        """Build a timeout error ToolResult with context-aware message."""
        context = " after reconnect" if after_reconnect else ""
        logger.warning(
            "[MCP] call_tool %s timed out for tenants=%s%s (lock=%ss, exec=%ss)",
            name,
            tenant_ids,
            context,
            settings.mcp_lock_acquire_timeout,
            settings.mcp_tool_execution_timeout,
        )
        suffix = " after reconnect" if after_reconnect else ""
        reminder_suffix = " после переподключения" if after_reconnect else ""
        return ToolResult(
            tool_content=json.dumps(
                {
                    "ok": False,
                    "error": (
                        f"Tool call timed out{suffix} "
                        f"(lock timeout {settings.mcp_lock_acquire_timeout}s, "
                        f"exec timeout {settings.mcp_tool_execution_timeout}s)"
                    ),
                },
                ensure_ascii=False,
            ),
            reminder=f"Инструмент {name} не выполнен: таймаут{reminder_suffix}.",
            ok=False,
            error=f"Tool call timed out{suffix}",
        )

    async def _execute_tool_call(
        self,
        conn: _TenantConnection,
        name: str,
        arguments: dict[str, Any],
        tenant_ids: list[str],
        *,
        context_label: str = "",
    ) -> Any:
        """Acquire the call lock and execute a single tool call.

        Args:
            conn: The tenant connection to use.
            name: Tool name.
            arguments: Tool arguments.
            tenant_ids: Tenant IDs for logging.
            context_label: Extra label for log (e.g. 'after reconnect').

        Returns:
            Raw MCP CallToolResult.

        Raises:
            TimeoutError: If lock acquisition or execution times out.
            Exception: Any other MCP SDK error.
        """
        async with asyncio.timeout(settings.mcp_lock_acquire_timeout):
            async with conn.call_lock:
                logger.info(
                    "[MCP] Calling tool %s for tenants=%s%s with args=%s",
                    name,
                    tenant_ids,
                    f" ({context_label})" if context_label else "",
                    arguments,
                )
                async with asyncio.timeout(settings.mcp_tool_execution_timeout):
                    result = await conn.session.call_tool(name, arguments)
                    conn.last_used = time.monotonic()
                    # Log result size for abuse detection
                    result_size = sum(
                        len(getattr(b, "text", ""))
                        for b in result.content
                        if getattr(b, "type", None) == "text"
                    )
                    label = f" {context_label}" if context_label else ""
                    logger.info(
                        "[MCP] Tool %s completed%s: %d content blocks, %d chars total",
                        name,
                        label,
                        len(result.content),
                        result_size,
                    )
                    return result

    # -- public API -------------------------------------------------------------

    async def call_tool(
        self, session: "_SessionProxy", name: str, arguments: dict[str, Any]
    ) -> ToolResult:
        """Call an MCP tool over the live session and return a pre-built ToolResult.

        Preserves the result-processing behaviour of the previous REST-bridge
        version (unwrapping JSON, building reminders) so downstream prompting
        logic doesn't need to change.
        """
        try:
            conn = await self._get_connection(session.tenant_ids)
        except ConnectionError as exc:
            # Circuit breaker open
            logger.warning(
                "[MCP] call_tool %s blocked by circuit breaker for tenants=%s: %s",
                name,
                session.tenant_ids,
                exc,
            )
            return ToolResult(
                tool_content=json.dumps(
                    {"ok": False, "error": str(exc)}, ensure_ascii=False
                ),
                reminder=(
                    f"Инструмент {name} временно недоступен после {settings.mcp_max_consecutive_failures} "
                    f"неудачных попыток. Попробуйте позже."
                ),
                ok=False,
                error=str(exc),
            )

        try:
            result = await self._execute_tool_call(
                conn, name, arguments, session.tenant_ids
            )
            self._mark_success(conn)
        except TimeoutError:
            self._mark_failure(conn)
            return self._timeout_error_result(name, session.tenant_ids)
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
            self._mark_failure(conn)
            logger.warning(
                "[MCP] call_tool %s failed for tenants=%s, reconnecting: %s",
                name,
                session.tenant_ids,
                exc,
            )
            try:
                conn = await self._reconnect(session.tenant_ids)
                result = await self._execute_tool_call(
                    conn,
                    name,
                    arguments,
                    session.tenant_ids,
                    context_label="after reconnect",
                )
                self._mark_success(conn)
            except TimeoutError:
                self._mark_failure(conn)
                return self._timeout_error_result(
                    name, session.tenant_ids, after_reconnect=True
                )
            except Exception as exc2:
                self._mark_failure(conn)
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

        logger.info(
            "[MCP] _build_tool_result for %s: isError=%s, result_length=%d, preview=%s...",
            name,
            getattr(result, "isError", False),
            len(raw_text),
            raw_text[:150],
        )

        if getattr(result, "isError", False):
            error_text = raw_text or "Unknown error"
            return ToolResult(
                tool_content=json.dumps(
                    {"ok": False, "error": error_text}, ensure_ascii=False
                ),
                reminder=(
                    f"[TOOL_ERROR] '{name}' FAILED: {error_text[:250]}. "
                    "You MUST pass a non-empty 'pattern' parameter! "
                    f"Example: {name}(pattern='your search query'). "
                    "NEVER call with empty arguments."
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
            parsed = None
            flat = raw_text

        # If result has empty_hint, append it to the reminder
        reminder_text = f"Инструмент {name} вернул данные: {flat[:200]}. "
        if (
            isinstance(parsed, dict)
            and parsed.get("total", 0) == 0
            and parsed.get("empty_hint")
        ):
            hint = parsed["empty_hint"]
            action = hint.get("suggested_action", "")
            values = hint.get("available_values", {})
            if action:
                reminder_text += f"\n\nWARNING: No results found. {action}"
            if values:
                reminder_text += (
                    f"\nAvailable values: {json.dumps(values, ensure_ascii=False)}"
                )
        else:
            reminder_text += "ОБЯЗАТЕЛЬНО покажи эти данные пользователю."

        return ToolResult(
            tool_content=flat,
            reminder=reminder_text,
            ok=True,
        )

    async def close(self) -> None:
        """Close all open tenant sessions and stop GC."""
        # Stop GC first
        await self.stop_gc()

        async with self._registry_lock:
            conns = list(self._connections.values())
            self._connections.clear()
        for conn in conns:
            try:
                await conn.close()
            except Exception:
                logger.warning("[MCP] Error closing connection for %s", conn.tenant_id)
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

    async def get_schema(self) -> dict | None:
        return await self.client.get_schema(self.tenant_ids)

    async def get_display_name(self, tool_name: str) -> str | None:
        """Return the user-facing display name for a tool, or None if not available."""
        return await self.client.get_display_name(self.tenant_ids, tool_name)
