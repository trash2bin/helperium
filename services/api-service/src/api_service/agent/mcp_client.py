"""MCP (Model Context Protocol) client for tool interaction.

HTTP routes called:
    _open_connection() -> mcp-gateway:/mcp (Streamable HTTP handshake)
    call_tool()        -> transport-managed JSON-RPC tools/call
    list_tools()       -> transport-managed JSON-RPC tools/list
    _reconnect()       -> new Streamable HTTP MCP session

MCP transport: standard Streamable HTTP using the official Python `mcp` v2
SDK. The gateway exposes one endpoint (`/mcp`) and mcp-go owns MCP session
lifecycle and response delivery.

Multi-tenancy: the client adds an already-resolved `X-Tenant-ID` scope and an
optional service Bearer credential to every transport request. The gateway
creates a separate stateful handler for each tenant set; generated tool
closures retain the resolved tenant identity when calling data-service.

One persistent v2 Client connection is kept per tenant set. A lock serializes
tool calls per connection so conversation-level tool ordering remains stable.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Coroutine

import httpx
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from api_service.prometheus_metrics import (
    mcp_connection_quarantines_total,
    mcp_reconnects_total,
    mcp_tool_timeouts_total,
)

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
#   MCP_HTTP_TIMEOUT              (default: 10.0)
#   MCP_HTTP_READ_TIMEOUT         (default: 1800.0)
#   MCP_SESSION_INIT_TIMEOUT      (default: 15.0)
#
# Failure-escalation constants (regression: gateway death mid-call left
# cancelled-but-suppressed SDK tasks behind; each stuck task made the event
# loop re-schedule itself forever — a 100%-of-one-core busy spin that health
# checks cannot see). See _execute_tool_call and _TenantConnection.close.
#   MCP_CLOSE_ESCALATION_TIMEOUT  (default: 5.0)
#     Grace period given to a session_ctx __aexit__ before the transport is
#     force-closed from the outside.
#   MCP_ZOMBIE_TOOL_TIMEOUTS      (default: 2)
#     Consecutive timed-out tool calls tolerated on one connection before it is
#     considered zombie-haunted and torn down instead of reused.


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
    error_code: str | None = None


@dataclass(slots=True)
class _TenantConnection:
    """Holds the live streamable-HTTP transport + session for one tenant.

    Lifecycle ownership: ``Client.__aexit__`` unwinds an anyio cancel scope
    that must be exited from the SAME task that entered it. Entering in one
    task (e.g. a request handler) and exiting in another (GC, lifespan,
    quarantine) raises "Attempted to exit cancel scope in a different task"
    and leaves the SDK task group half-alive — which in turn can keep
    re-scheduling itself forever (the 100%-CPU busy-spin regression).

    Therefore the context is entered by a dedicated owner task (``_owner``)
    created in :meth:`spawn_owner`; every teardown path calls :meth:`close`,
    which just signals that task and waits for it to finish the unwind in its
    own task, bounded by an escalation timeout.
    """

    tenant_id: str
    # Official MCP SDK v2 Client owns the Streamable HTTP transport and performs
    # protocol negotiation/initialization on context entry. Published by the
    # owner task once ``Client.__aenter__`` succeeds (loose typing because the
    # pre-handshake lifetime genuinely holds ``None``).
    session: Any = None
    session_ctx: Any = None  # the Client(...) async context manager
    transport_http_client: Any | None = None  # owned httpx2 client for Streamable HTTP
    call_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    list_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    tool_display_names: dict[str, str] = field(default_factory=dict)
    schema: dict | None = None  # LLM-friendly schema description (from /mcp/schema)
    last_used: float = field(default_factory=time.monotonic)
    # Zombie-task tracking: consecutive tool calls that hit the execution
    # timeout on THIS connection. When cancelled, an SDK call_tool task can
    # suppress cancellation and keep spinning in its anyio task group; after
    # repeated timeouts we stop reusing this connection entirely so the stuck
    # task group is discarded with its owner (see close()).
    consecutive_tool_timeouts: int = 0
    # Owner-task plumbing (created via spawn_owner())
    _close_event: asyncio.Event | None = None
    _closed_done: asyncio.Event | None = None
    _open_done: asyncio.Event | None = None
    _open_exc: BaseException | None = None
    _owner_task: asyncio.Task | None = None

    def spawn_owner(self) -> None:
        """Create lifecycle plumbing and start the owner task.

        If ``session`` is already set (tests / externally-opened contexts) the
        owner handles teardown only. Otherwise the owner performs
        ``Client.__aenter__`` itself: anyio requires each cancel scope to be
        EXITED from the task that ENTERED it, so both halves of the context
        lifecycle must live in one task — the owner.
        """
        self._close_event = asyncio.Event()
        self._closed_done = asyncio.Event()
        self._open_done = asyncio.Event()
        self._owner_task = asyncio.create_task(
            self._owner_lifecycle(), name=f"mcp-owner-{self.tenant_id or 'default'}"
        )

    async def _owner_lifecycle(self) -> None:
        assert (
            self._close_event is not None
            and self._closed_done is not None
            and self._open_done is not None
        )
        if self.session is None:
            # Own the OPEN side too — same-task rule covers both ends.
            try:
                async with asyncio.timeout(settings.mcp_session_init_timeout):
                    self.session = await self.session_ctx.__aenter__()
            except Exception as exc:  # noqa: BLE001 - surfaced via wait_opened()
                self._open_exc = exc
                # A failed __aenter__ unwinds its own resources per the SDK,
                # but the raw httpx2 client is ours — make sure sockets die.
                if self.transport_http_client is not None:
                    with contextlib.suppress(Exception):
                        await self.transport_http_client.aclose()
                self._open_done.set()
                self._closed_done.set()
                return
        self._open_done.set()

        try:
            await self._close_event.wait()
            await self._exit_session_bounded()
        except asyncio.CancelledError:
            # The whole loop is going down; still try to exit in THIS task.
            with contextlib.suppress(Exception):
                await self._exit_session_bounded()
            raise
        finally:
            self._closed_done.set()

    async def wait_opened(self) -> Any:
        """Block until the owner finished the handshake; re-raise open errors."""
        assert self._open_done is not None
        await self._open_done.wait()
        if self._open_exc is not None:
            raise self._open_exc
        if self.session is None:  # pragma: no cover - defensive
            raise RuntimeError("MCP owner finished but no session was published")
        return self.session

    async def _exit_session_bounded(self) -> None:
        """Run session_ctx.__aexit__ here (owner task) with escalation fallback.

        Single bounded attempt: if the SDK teardown exceeds the escalation
        timeout we force-close the raw transport sockets and move on. Retrying
        ``__aexit__`` against sockets we just closed cannot succeed and only
        prolongs shutdown (one-off leak beats permanent CPU spin).
        """
        escalation_timeout = settings.mcp_close_escalation_timeout
        try:
            async with asyncio.timeout(escalation_timeout):
                await self.session_ctx.__aexit__(None, None, None)
        except (TimeoutError, asyncio.TimeoutError):
            logger.warning(
                "[MCP] session exit for tenants=%s exceeded %.1fs; forcing transport close",
                self.tenant_id or "(default)",
                escalation_timeout,
            )
        except Exception:
            logger.debug(
                "[MCP] session exit for tenants=%s raised",
                self.tenant_id or "(default)",
                exc_info=True,
            )
        finally:
            if self.transport_http_client is not None:
                with contextlib.suppress(Exception):
                    await self.transport_http_client.aclose()

    async def close(self) -> None:
        """Signal the owner task to unwind the session (bounded total wait).

        Safe to call multiple times and from any task: the actual
        ``__aexit__`` always runs inside the owner, satisfying anyio's
        same-task rule.
        """
        if self._close_event is None or self._closed_done is None:
            return
        if self._close_event.is_set():
            return
        self._close_event.set()
        total_wait = settings.mcp_close_escalation_timeout * 3
        done, pending = await asyncio.wait(
            {asyncio.ensure_future(self._closed_done.wait())},
            timeout=total_wait,
        )
        for p in pending:
            p.cancel()
        if pending:
            logger.error(
                "[MCP] owner task for tenants=%s did not finish within %.1fs",
                self.tenant_id or "(default)",
                total_wait,
            )
            # Last resort without breaking anyio ownership rules: kill the
            # sockets so whatever is spinning loses its I/O.
            if self.transport_http_client is not None:
                with contextlib.suppress(Exception):
                    await self.transport_http_client.aclose()


class _CircuitBreakerOpen(ConnectionError):
    """Raised when the per-tenant circuit is open and reconnects are skipped."""


class MCPClient:
    """Maintains one persistent MCP connection per tenant scope.

    Public API is unchanged from the previous REST-bridge version:
    `get_session()`, `list_tools(session)`, `call_tool(session, name, args)`,
    `close()` — so callers elsewhere in the codebase don't need to change.
    """

    def __init__(self) -> None:
        self._connections: dict[str, _TenantConnection] = {}
        # Circuit-breaker evidence keyed by tenant scope, tracked separately
        # from live connections. A cold handshake on a dead gateway has no
        # registry entry to hang failure counts on (and idle-reconnect deletes
        # the entry before failing), so without this store the breaker never
        # accumulates and every request pays the full init timeout.
        self._breaker_state: dict[str, tuple[int, float]] = {}
        self._registry_lock = asyncio.Lock()
        # GC background task
        self._gc_task: asyncio.Task | None = None
        self._gc_started: bool = False
        # Strong references to fire-and-forget tasks: the event loop keeps only
        # weak references to tasks, so unreferenced detached tasks can be
        # garbage-collected mid-flight (RUF006). Kept in a set and discarded via
        # done callbacks.
        self._detached_tasks: set[asyncio.Task[None]] = set()

    def _spawn_detached(self, coro: Coroutine[Any, Any, None], label: str) -> None:
        """Schedule a background task holding a strong reference until done."""
        task = asyncio.create_task(coro, name=label)
        self._detached_tasks.add(task)
        task.add_done_callback(self._detached_tasks.discard)

    async def _drain_detached_tasks(self, timeout: float = 15.0) -> None:
        """Wait for pending detached tasks (used on shutdown)."""
        pending = [t for t in self._detached_tasks if not t.done()]
        if not pending:
            return
        await asyncio.wait(pending, timeout=timeout)
        for t in self._detached_tasks:
            if not t.done():
                t.cancel()

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
        if settings.mcp_client_api_key:
            headers["Authorization"] = f"Bearer {settings.mcp_client_api_key}"

        logger.info(
            "[MCP] Opening Streamable HTTP connection for tenants=%s",
            tenant_key or "(default)",
        )
        # The standard v2 transport carries request/response JSON-RPC over one
        # endpoint. Keep the client per tenant connection so headers, pooling
        # and lifecycle remain tenant-scoped.
        transport_http_client = httpx2.AsyncClient(
            headers=headers,
            timeout=httpx2.Timeout(
                settings.mcp_http_timeout,
                read=settings.mcp_http_read_timeout,
            ),
            follow_redirects=True,
        )
        transport = streamable_http_client(
            settings.mcp_streamable_http_url,
            http_client=transport_http_client,
        )

        # mcp-go gateway implements the Streamable HTTP initialize handshake,
        # but not the post-handshake `server/discover` negotiation introduced by
        # the v2 Python SDK. Force the standards-compatible legacy handshake
        # until both ends support a common modern discovery protocol.
        session_ctx = Client(transport, mode="legacy")

        logger.info("[MCP] Session ready pending (opening via owner task)")
        conn = _TenantConnection(
            tenant_id=tenant_key,
            session=None,
            session_ctx=session_ctx,
            transport_http_client=transport_http_client,
        )
        conn.spawn_owner()
        try:
            await conn.wait_opened()
            logger.info("[MCP] Session ready for tenants=%s", tenant_key or "(default)")
            await self._enrich_connection(conn, headers, tenant_key)
        except (Exception, asyncio.CancelledError):
            # Covers plain failures AND request cancellation while blocked in
            # wait_opened()/GETs: without this, a cancelled open leaks a live
            # MCP session, transport sockets and the owner task until restart.
            self._spawn_detached(
                self._close_redundant_connection(conn),
                f"mcp-close-redundant-{tenant_key or 'default'}",
            )
            raise

        return conn

    async def _enrich_connection(
        self, conn: _TenantConnection, headers: dict[str, str], tenant_key: str
    ) -> None:
        """Fetch tool display-name mapping and LLM schema for a fresh connection.

        Both fetches are best-effort: failures degrade to fallbacks and never
        fail the connection itself.
        """
        # ── Load tool display_name mapping from mcp-gateway ────────────
        try:
            async with httpx.AsyncClient(timeout=5.0) as hclient:
                url = settings.mcp_gateway_url + "/mcp/tools/mapping"
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
                url = settings.mcp_gateway_url + "/mcp/schema"
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

    def _breaker_key(self, tenant_ids: list[str] | str) -> str:
        """Normalize a tenant scope to the registry/breaker key."""
        return ",".join(tenant_ids) if isinstance(tenant_ids, list) else tenant_ids

    @staticmethod
    def _as_int(value: Any, default: int = 0) -> int:
        """Coerce possibly-degraded counter values (test doubles, partial
        deserialisation) into a plain int; the breaker must never crash."""
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _load_breaker(self, key: str) -> tuple[int, float]:
        """Read failure evidence for a tenant scope.

        ``_breaker_state`` is the single source of truth: the registry may be
        empty (cold handshake) or freshly replaced (reconnect), so failure
        evidence must survive connection churn in its own dict.
        """
        return self._breaker_state.get(key, (0, 0.0))

    def _store_breaker(self, key: str, failures: int, last_failure: float) -> None:
        """Write failure evidence for a tenant scope (0 failures clears it)."""
        if self._as_int(failures) <= 0:
            self._breaker_state.pop(key, None)
            return
        self._breaker_state[key] = (self._as_int(failures), float(last_failure))

    def _is_circuit_open(self, conn: _TenantConnection, key: str | None = None) -> bool:
        """Check if circuit breaker is open for this tenant scope.

        Open means: consecutive_failures >= MAX and not enough time has passed
        since the last failure. The connection argument is kept for API
        compatibility with test doubles; evidence always comes from the store.
        """
        stored_key = key if key is not None else conn.tenant_id
        failures, last_failure = self._load_breaker(stored_key)
        if failures < settings.mcp_max_consecutive_failures:
            return False
        elapsed = time.monotonic() - last_failure
        return elapsed < settings.mcp_circuit_breaker_timeout

    def _mark_success(self, conn: _TenantConnection) -> None:
        """Reset circuit breaker on successful operation."""
        self._store_breaker(conn.tenant_id, 0, 0.0)

    def _mark_failure(self, conn: _TenantConnection) -> None:
        """Increment circuit breaker for the connection's tenant scope."""
        prev_failures, _ = self._load_breaker(conn.tenant_id)
        self._store_breaker(
            conn.tenant_id,
            max(self._as_int(prev_failures) + 1, 1),
            time.monotonic(),
        )

    def _mark_failure_if_tracked(
        self, conn: _TenantConnection | None, tenant_key: list[str] | str
    ) -> None:
        """Record a failure for the tenant scope when no live connection
        matches it (cold handshake on a dead gateway, idle reconnect).

        Evidence lives solely in ``_breaker_state``, so accumulation works even
        though no registry entry exists to hang counters on.
        """
        key = self._breaker_key(tenant_key)
        if conn is not None and conn.tenant_id == key:
            self._mark_failure(conn)
            return
        prev_failures, _ = self._load_breaker(key)
        self._store_breaker(key, max(prev_failures + 1, 1), time.monotonic())

    async def _get_connection(
        self, tenant_ids: list[str] | None = None
    ) -> _TenantConnection:
        """Return a healthy cached connection or open a new one.

        Concurrency note: ``_open_connection`` performs a network handshake
        (potentially tens of seconds), so it MUST run OUTSIDE the registry
        lock — otherwise a cold-connecting tenant head-of-line blocks every
        other tenant, including cache hits. Mirrors ``_reconnect``: mutate
        the registry under the lock, do I/O outside it.
        """
        tenant_key = ",".join(tenant_ids) if tenant_ids else ""

        # Cold path must consult the breaker store BEFORE anything else.
        # Evidence survives connection churn (registry may be empty because
        # the gateway died and quarantine/reconnect removed the entry); if
        # the circuit is open we fast-fail instead of paying a full
        # mcp_session_init_timeout per request while the gateway is down.
        failures, last_failure = self._load_breaker(tenant_key)
        if failures >= settings.mcp_max_consecutive_failures and (
            time.monotonic() - last_failure < settings.mcp_circuit_breaker_timeout
        ):
            logger.warning(
                "[MCP] Circuit breaker open for tenants=%s (%d failures, %.1fs ago), "
                "fast-failing cold open",
                tenant_key or "(default)",
                failures,
                time.monotonic() - last_failure,
            )
            raise _CircuitBreakerOpen(
                f"Circuit breaker open for {tenant_key or '(default)'}: "
                f"{failures} consecutive failures, "
                f"retry in {settings.mcp_circuit_breaker_timeout - (time.monotonic() - last_failure):.0f}s"
            )

        stale_conn: _TenantConnection | None = None
        async with self._registry_lock:
            conn = self._connections.get(tenant_key)
            if conn is not None:
                # Circuit breaker: if open, raise immediately
                if self._is_circuit_open(conn, key=tenant_key):
                    stored_failures, stored_last = self._load_breaker(tenant_key)
                    logger.warning(
                        "[MCP] Circuit breaker open for tenants=%s "
                        "(%d failures, %.1fs ago), skipping reconnect",
                        tenant_key or "(default)",
                        stored_failures,
                        time.monotonic() - stored_last,
                    )
                    raise _CircuitBreakerOpen(
                        f"Circuit breaker open for {tenant_key or '(default)'}: "
                        f"{stored_failures} consecutive failures, "
                        f"retry in {settings.mcp_circuit_breaker_timeout - (time.monotonic() - stored_last):.0f}s"
                    )

                # Session idle > 4 min — reconnect proactively
                idle = time.monotonic() - conn.last_used
                if idle > 240:
                    logger.info(
                        "[MCP] Session for tenants=%s idle %.0fs, reconnecting",
                        tenant_key or "(default)",
                        idle,
                    )
                    del self._connections[tenant_key]
                    stale_conn = conn
                else:
                    return conn

        # Outside the lock: tear down the stale connection, then open.
        # (Owner-task close is bounded and non-blocking for others.)
        if stale_conn is not None:
            await stale_conn.close()

        new_conn = await self._open_connection(tenant_ids)

        async with self._registry_lock:
            existing = self._connections.get(tenant_key)
            if existing is None:
                self._connections[tenant_key] = new_conn
                self._store_breaker(tenant_key, 0, 0.0)
                return new_conn
            # Lost a race with a concurrent opener for the same tenant: keep
            # the winner, close our redundant connection.
            redundant = new_conn
            winner = existing
        self._spawn_detached(
            self._close_redundant_connection(redundant),
            f"mcp-close-redundant-{redundant.tenant_id or 'default'}",
        )
        return winner

    async def _close_redundant_connection(self, conn: _TenantConnection) -> None:
        """Close a losing race-connection quietly."""
        try:
            await conn.close()
        except Exception:
            logger.warning(
                "[MCP] error closing redundant connection for tenants=%s",
                conn.tenant_id or "(default)",
            )

    async def _quarantine_connection(self, conn: _TenantConnection) -> None:
        """Remove a possibly zombie-haunted connection and close it with escalation.

        Runs detached (fire-and-forget) so the tool-call path never blocks on a
        hung SDK teardown. Idempotent: if the connection was already replaced by
        a reconnect, closing the stale object is still safe.
        """
        removed = False
        async with self._registry_lock:
            for key, existing in list(self._connections.items()):
                if existing is conn:
                    del self._connections[key]
                    removed = True
                    break
        # A zombie means the dependency is unhealthy; the next cold open must
        # not start from a clean slate. Evidence goes straight to the store.
        prev_failures, _ = self._load_breaker(conn.tenant_id)
        self._store_breaker(
            conn.tenant_id,
            max(prev_failures + 1, 1),
            time.monotonic(),
        )
        try:
            # Same bounded owner-task close path the reconnect-race loser uses;
            # quarantine must never block or fail the caller either way.
            await self._close_redundant_connection(conn)
            logger.warning(
                "[MCP] quarantined connection for tenants=%s (removed_from_registry=%s)",
                conn.tenant_id or "(default)",
                removed,
            )
        except Exception:
            logger.exception(
                "[MCP] failed to close quarantined connection for tenants=%s",
                conn.tenant_id or "(default)",
            )

    async def _reconnect(
        self, tenant_ids: list[str] | None = None
    ) -> _TenantConnection:
        """Eagerly replace the connection for a tenant scope.

        Not folded into ``_quarantine_connection`` on purpose: quarantine is a
        fire-and-forget eviction (no replacement is opened while the gateway
        looks dead — the next ``_get_connection`` pays the cold handshake),
        whereas reconnect must return a live connection to its caller. Both
        paths now share the same primitives: pop/remove under the registry
        lock, tenant-keyed breaker evidence, detached bounded owner-task close.
        """
        mcp_reconnects_total.inc()
        tenant_key = ",".join(tenant_ids) if tenant_ids else ""

        # Consult the breaker before paying a full handshake: the caller
        # (call_tool retry path) has usually just recorded another failure,
        # so an already-open circuit must fast-fail here too.
        failures, last_failure = self._load_breaker(tenant_key)
        if failures >= settings.mcp_max_consecutive_failures and (
            time.monotonic() - last_failure < settings.mcp_circuit_breaker_timeout
        ):
            logger.warning(
                "[MCP] Reconnect skipped, circuit breaker open for tenants=%s "
                "(%d failures, %.1fs ago)",
                tenant_key or "(default)",
                failures,
                time.monotonic() - last_failure,
            )
            raise _CircuitBreakerOpen(
                f"Circuit breaker open for {tenant_key or '(default)'}: "
                f"{failures} consecutive failures, "
                f"retry in {settings.mcp_circuit_breaker_timeout - (time.monotonic() - last_failure):.0f}s"
            )

        async with self._registry_lock:
            old = self._connections.pop(tenant_key, None)
        if old is not None:
            # Closing a dropped connection can hang on a haunted SDK task;
            # it must not delay or fail the reconnect for this tenant.
            self._spawn_detached(
                self._close_redundant_connection(old),
                f"mcp-close-redundant-{tenant_key or 'default'}",
            )

        try:
            conn = await self._open_connection(tenant_ids)
            # Reset circuit breaker on successful reconnect
            self._store_breaker(tenant_key, 0, 0.0)
        except Exception:
            # Reconnect failed — persist evidence in the tenant-keyed store so
            # the breaker accumulates even though the registry entry is gone.
            old_failures, _ = self._load_breaker(tenant_key)
            logger.warning(
                "[MCP] Reconnect failed for tenants=%s (accumulated %d failures)",
                tenant_key or "(default)",
                old_failures + 1,
            )
            self._store_breaker(tenant_key, old_failures + 1, time.monotonic())
            raise

        async with self._registry_lock:
            existing = self._connections.get(tenant_key)
            if existing is None:
                self._connections[tenant_key] = conn
                return conn
            # Lost a race with a concurrent reconnect for the same tenant:
            # keep the winner, close our redundant connection.
            redundant = conn
            winner = existing
        self._spawn_detached(
            self._close_redundant_connection(redundant),
            f"mcp-close-redundant-{redundant.tenant_id or 'default'}",
        )
        return winner

    # -- public API -------------------------------------------------------------

    @contextlib.asynccontextmanager
    async def get_session(
        self,
        tenant_ids: list[str] | None = None,
        disconnect_check: Callable[[], bool] | None = None,
    ):
        """Async context manager providing a session proxy for specific tenant(s).

        ``disconnect_check`` (when provided) is a zero-argument callable that
        returns True once the HTTP client that started this turn has gone away.
        It lets call_tool distinguish real client disconnects from MCP SDK
        internal cancellation blips.
        """
        proxy = _SessionProxy(
            self, tenant_ids=tenant_ids or [], disconnect_check=disconnect_check
        )
        try:
            yield proxy
        finally:
            pass

    async def _list_tools_bounded(self, conn: _TenantConnection) -> Any:
        """Run one bounded list_tools call with split budgets (mirrors call_tool).

        Lock acquisition is capped by the short ``mcp_lock_acquire_timeout``;
        the listing itself gets the full ``mcp_tool_execution_timeout``.
        Sharing a single timeout between both phases would silently truncate
        listings with a misleading "timed out waiting for list lock" log.
        """
        try:
            async with asyncio.timeout(settings.mcp_lock_acquire_timeout):
                await conn.list_lock.acquire()
        except TimeoutError:
            raise
        try:
            # Hard-deadline runner (shared with call_tool): a listing that
            # outlives its budget is cancelled for real; return-by-cap even
            # if the SDK suppresses cancellation.
            return await self._run_with_hard_deadline(
                conn.session.list_tools(),
                settings.mcp_tool_execution_timeout,
                f"list_tools tenants={conn.tenant_id or '(default)'}",
            )
        finally:
            conn.list_lock.release()

    async def list_tools(self, session: "_SessionProxy") -> list[dict[str, Any]]:
        """List available MCP tools for the tenant(s) over the live session.

        Uses its own ``list_lock`` so that ``list_tools`` never blocks
        a concurrent ``call_tool`` and vice versa.
        Handshake failures degrade to an empty tool list (safe public
        behaviour) instead of leaking raw transport exceptions.
        """
        try:
            conn = await self._get_connection(session.tenant_ids)
        except Exception as exc:
            logger.warning(
                "[MCP] list_tools could not obtain connection for tenants=%s: %s",
                session.tenant_ids,
                type(exc).__name__,
            )
            self._mark_failure_if_tracked(None, session.tenant_ids)
            return []
        try:
            result = await self._list_tools_bounded(conn)
            conn.last_used = time.monotonic()
        except TimeoutError:
            # С разнесёнными бюджетами это может быть либо ожидание лока,
            # либо само листинг — не утверждаем какое; оба гасим одинаково.
            logger.warning(
                "[MCP] list_tools timed out for tenants=%s", session.tenant_ids
            )
            return []
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
            try:
                conn = await self._reconnect(session.tenant_ids)
                result = await self._list_tools_bounded(conn)
            except Exception as exc2:
                # Symmetric sanitisation with call_tool: any failure on the
                # retry path (handshake, transport, timeout) degrades to an
                # empty tool list instead of a raw exception into the loop.
                self._mark_failure_if_tracked(None, session.tenant_ids)
                logger.warning(
                    "[MCP] list_tools reconnect retry failed for tenants=%s: %s",
                    session.tenant_ids,
                    type(exc2).__name__,
                )
                return []

        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": tool.input_schema or {},
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

    @staticmethod
    def _connection_interrupted_result(name: str) -> ToolResult:
        """Return a stable public result when the MCP transport is interrupted."""
        message = "Tool connection was interrupted; retry shortly."
        logger.warning("[MCP] call_tool %s interrupted by transport", name)
        return ToolResult(
            tool_content=json.dumps(
                {"ok": False, "error": message}, ensure_ascii=False
            ),
            reminder=(
                f"Инструмент {name} временно недоступен из-за обрыва соединения. "
                "Не повторяй тот же вызов сразу; сообщи пользователю, что данные временно недоступны."
            ),
            ok=False,
            error=message,
        )

    @staticmethod
    def _consume_background_task_result(task: asyncio.Task[Any]) -> None:
        """Consume a detached timed-out MCP task to avoid unhandled warnings."""
        with contextlib.suppress(asyncio.CancelledError, Exception):
            task.result()

    async def _run_with_hard_deadline(
        self, coro: Coroutine[Any, Any, Any], timeout: float, label: str
    ) -> Any:
        """Run ``coro`` under a HARD deadline the SDK cannot out-wait.

        The MCP SDK's Streamable HTTP background receiver can suppress
        cancellation while it attempts to reconnect. A plain
        ``asyncio.wait_for``/``asyncio.timeout`` then blocks forever waiting
        for the cancelled coroutine to finish. This runner waits with
        ``asyncio.wait`` instead: on timeout (or interruption) the child is
        cancelled, its result consumed detached, and control returns to the
        caller at/before ``timeout`` no matter what the SDK does.
        """
        task = asyncio.create_task(coro)
        try:
            done, _ = await asyncio.wait({task}, timeout=timeout)
        except BaseException:
            current_task = asyncio.current_task()
            logger.warning(
                "[MCP] %s wait interrupted by parent cancellation "
                "(parent cancelling=%s)",
                label,
                current_task.cancelling() if current_task is not None else 0,
            )
            if not task.done():
                task.cancel()
                task.add_done_callback(self._consume_background_task_result)
            raise
        if not done:
            task.cancel()
            task.add_done_callback(self._consume_background_task_result)
            logger.warning("[MCP] %s exceeded %.1fs hard deadline", label, timeout)
            raise TimeoutError
        return task.result()

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
        # Lock-acquisition phase is bounded separately from tool execution.
        # Previously a single asyncio.timeout wrapped BOTH phases with the
        # (shorter) lock timeout, silently capping every tool run at
        # mcp_lock_acquire_timeout and making MCP_TOOL_EXECUTION_TIMEOUT
        # unreachable.
        try:
            async with asyncio.timeout(settings.mcp_lock_acquire_timeout):
                await conn.call_lock.acquire()
        except TimeoutError:
            raise

        try:
            logger.info(
                "[MCP] Calling tool %s for tenants=%s%s (argument_count=%d)",
                name,
                tenant_ids,
                f" ({context_label})" if context_label else "",
                len(arguments),
            )
            # Hard-deadline runner shared with list_tools: even if the SDK
            # suppresses cancellation, this chat request returns by the cap.
            try:
                result = await self._run_with_hard_deadline(
                    conn.session.call_tool(name, arguments),
                    settings.mcp_tool_execution_timeout,
                    f"call_tool {name} tenants={','.join(tenant_ids) or '(default)'}",
                )
            except TimeoutError:
                # Zombie escalation: a timed-out SDK task may have suppressed
                # cancellation and keep spinning inside its anyio task group.
                # After repeated timeouts on the same connection, tear the
                # connection down instead of leaving the haunted session in
                # the registry (regression: 100%-CPU event-loop spin).
                conn.consecutive_tool_timeouts += 1
                tenant_label = ",".join(tenant_ids) or "(default)"
                mcp_tool_timeouts_total.labels(tenant_label).inc()
                if conn.consecutive_tool_timeouts >= settings.mcp_zombie_tool_timeouts:
                    logger.error(
                        "[MCP] %d consecutive tool timeouts for tenants=%s; "
                        "force-closing possibly zombie connection",
                        conn.consecutive_tool_timeouts,
                        tenant_ids,
                    )
                    mcp_connection_quarantines_total.inc()
                    self._spawn_detached(
                        self._quarantine_connection(conn),
                        f"mcp-quarantine-{conn.tenant_id or 'default'}",
                    )
                raise

            conn.last_used = time.monotonic()
            conn.consecutive_tool_timeouts = 0
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
        finally:
            conn.call_lock.release()

    # -- public API -------------------------------------------------------------

    async def call_tool(
        self,
        session: "_SessionProxy",
        name: str,
        arguments: dict[str, Any],
    ) -> ToolResult:
        """Call an MCP tool over the live session and return a pre-built ToolResult.

        Preserves the result-processing behaviour of the previous REST-bridge
        version (unwrapping JSON, building reminders) so downstream prompting
        logic doesn't need to change.

        Cancellation contract (do not wrap in your own deadline):
        on a transport-level ``CancelledError`` with the client still
        attached, this method clears the task's cancellation count via
        ``uncancel()`` and returns a retryable result. If a caller wrapped
        ``call_tool`` in its own ``asyncio.timeout``/``wait_for``, that outer
        deadline would be silently swallowed (no ``TimeoutError`` reaches its
        ``__aexit__``). Today no caller does this by design — keep it that
        way, or replace the blunt cancelling-count discrimination with an
        explicit source flag before adding one.
        """
        try:
            conn = await self._get_connection(session.tenant_ids)
        except _CircuitBreakerOpen as exc:
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
        except ConnectionError as exc:
            logger.warning(
                "[MCP] call_tool %s could not obtain connection for tenants=%s: %s",
                name,
                session.tenant_ids,
                type(exc).__name__,
            )
            self._mark_failure_if_tracked(conn=None, tenant_key=session.tenant_ids)
            return self._connection_interrupted_result(name)
        except Exception as exc:
            # Handshake/transport failures that are not the circuit breaker
            # (init timeout, httpx errors, ...) must still yield a sanitised,
            # retryable public result — never a raw exception into the loop.
            logger.warning(
                "[MCP] call_tool %s could not obtain connection for tenants=%s: %s",
                name,
                session.tenant_ids,
                type(exc).__name__,
            )
            self._mark_failure_if_tracked(conn=None, tenant_key=session.tenant_ids)
            return self._connection_interrupted_result(name)

        try:
            result = await self._execute_tool_call(
                conn, name, arguments, session.tenant_ids
            )
            self._mark_success(conn)
        except TimeoutError:
            self._mark_failure(conn)
            return self._timeout_error_result(name, session.tenant_ids)
        except asyncio.CancelledError:
            current_task = asyncio.current_task()
            client_gone = bool(session.disconnect_check and session.disconnect_check())
            logger.warning(
                "[MCP] call_tool %s received transport cancellation "
                "(task cancelling=%s, client_disconnected=%s)",
                name,
                current_task.cancelling() if current_task is not None else 0,
                client_gone,
            )
            if client_gone:
                # The HTTP client that owns this turn is really gone. Re-raise
                # so the producer dies instead of burning another provider
                # round-trip (with thinking tokens) for a disconnected reader
                # and persisting an invisible turn into the transcript.
                raise
            # The Streamable MCP SDK can cancel its caller task when its
            # background GET stream dies. That is a transport blip, not a
            # browser disconnect; clear the caught cancellation so the caller
            # can emit a retryable ToolResult.
            if current_task is not None and current_task.cancelling():
                current_task.uncancel()
            self._mark_failure(conn)
            return self._connection_interrupted_result(name)
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
            except asyncio.CancelledError:
                current_task = asyncio.current_task()
                client_gone = bool(
                    session.disconnect_check and session.disconnect_check()
                )
                logger.warning(
                    "[MCP] call_tool %s retry received transport cancellation "
                    "(task cancelling=%s, client_disconnected=%s)",
                    name,
                    current_task.cancelling() if current_task is not None else 0,
                    client_gone,
                )
                if client_gone:
                    raise
                if current_task is not None and current_task.cancelling():
                    current_task.uncancel()
                self._mark_failure(conn)
                return self._connection_interrupted_result(name)
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
            "[MCP] _build_tool_result for %s: is_error=%s, result_length=%d",
            name,
            getattr(result, "is_error", False),
            len(raw_text),
        )

        if getattr(result, "is_error", False):
            error_text = raw_text or "Unknown error"
            error_code: str | None = None
            try:
                parsed_error = json.loads(error_text)
            except (json.JSONDecodeError, TypeError):
                parsed_error = None
            if isinstance(parsed_error, dict):
                candidate_code = parsed_error.get("error_code")
                if isinstance(candidate_code, str) and candidate_code:
                    error_code = candidate_code
            return ToolResult(
                tool_content=json.dumps(
                    {
                        "ok": False,
                        "error": error_text,
                        "error_code": error_code,
                    },
                    ensure_ascii=False,
                ),
                reminder=f"[TOOL_ERROR] '{name}' FAILED.",
                ok=False,
                error=error_text,
                error_code=error_code,
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

        # Let in-flight quarantine/redundant-close tasks finish (bounded) so
        # they don't race connection teardown below.
        await self._drain_detached_tasks()

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
    """Simple proxy that carries the tenant_ids context and an optional
    client-disconnect probe propagated from the chat route."""

    def __init__(
        self,
        client: MCPClient,
        tenant_ids: list[str] | None = None,
        disconnect_check: Callable[[], bool] | None = None,
    ) -> None:
        self.client = client
        self.tenant_ids = tenant_ids or []
        # Returns True when the HTTP client that started this turn is gone.
        # None means the caller did not provide a probe (tests, non-SSE paths).
        self.disconnect_check = disconnect_check

    async def list_tools(self) -> list[dict[str, Any]]:
        return await self.client.list_tools(self)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        return await self.client.call_tool(self, name, arguments)

    async def get_schema(self) -> dict | None:
        return await self.client.get_schema(self.tenant_ids)

    async def get_display_name(self, tool_name: str) -> str | None:
        """Return the user-facing display name for a tool, or None if not available."""
        return await self.client.get_display_name(self.tenant_ids, tool_name)
