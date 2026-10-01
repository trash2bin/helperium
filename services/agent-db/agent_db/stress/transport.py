"""MCP Streamable HTTP transport for the load driver (§11).

Why not ``urllib``: it opens a fresh TCP connection per request, so the
generator would pay connect cost on every call and fill TIME_WAIT on a long
ladder - both land in the measurement. A session therefore owns a persistent
``http.client`` connection and one in-flight request at a time (the orchestrator
serialises a turn per session anyway, and a second concurrent call would measure
our own queue rather than the platform's).

The connection is injected as a factory so everything above the socket is
testable without a live stack: status codes, bodies, timeouts and a server that
drops the session are all inputs a test can produce.

Response framing. The gateway serves MCP through ``mcp-go`` Streamable HTTP,
which may answer a request either with a bare JSON body or with a one-event SSE
stream depending on negotiation, so both are parsed here. The transport does not
assume which one it gets.

Error classification is structural (§7): status codes, exception types and the
JSON-RPC envelope. Message text is never parsed - it is localized ru/en - and a
body excerpt is kept only for failures, since a successful tool call carries
tenant rows that must not reach an artifact.

What the live gateway does (verified 2026-09-27, re-checked on a native stand
2026-09-29):

- ``initialize`` answers 200 **with** an ``Mcp-Session-Id``: ``cmd/main.go`` builds
  the transport with ``server.WithStateful(true)``, so ``mcp-go`` stamps the header
  on the initialize response and every later request must carry it back. The ids
  observed on the stand look like ``mcp-session-<uuid>`` - literally the
  ``idPrefix`` of ``InsecureStatefulSessionIdManager``. The replay-on-404 path
  below is therefore the live guard against a session the server forgot, not a
  defence against a hypothetical deployment. (An earlier note here claimed the
  endpoint was stateless; the code always handled both, but the description was
  wrong and a reader would mis-read a reinitialisation count because of it.)
- ``tools/call`` answers ``application/json``, not ``text/event-stream``: the
  JSON branch is the live one and the SSE branch is defence against a
  negotiation change.
- ``notifications/initialized`` answers 202 with an empty body, which is why an
  empty body is an error only on a request.
- A reused connection is visibly cheaper per call than a fresh one, so the
  keep-alive is not cosmetic even on a loopback stand.
"""

from __future__ import annotations

import http.client
import json
import socket
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Protocol
from urllib.parse import urlsplit

from .records import ErrorClass

# Diagnostics only, and only for failures: enough to tell a gateway error from a
# tool error, far too little to reconstruct a tenant row.
BODY_HEAD_LIMIT = 300

PROTOCOL_VERSION = "2025-03-26"
CLIENT_NAME = "helperium-stress"
CLIENT_VERSION = "1.0"
SESSION_HEADER = "Mcp-Session-Id"


class HttpResponseLike(Protocol):
    status: int
    headers: Any

    def read(self) -> bytes: ...


class HttpConnectionLike(Protocol):
    def request(
        self,
        method: str,
        url: str,
        body: bytes | None = None,
        headers: dict | None = None,
    ) -> None: ...

    def getresponse(self) -> HttpResponseLike: ...

    def close(self) -> None: ...


ConnectionFactory = Callable[[str, str, int, float], HttpConnectionLike]


def _default_connection_factory(
    scheme: str, host: str, port: int, timeout: float
) -> HttpConnectionLike:
    if scheme == "https":
        return http.client.HTTPSConnection(host, port, timeout=timeout)
    return http.client.HTTPConnection(host, port, timeout=timeout)


@dataclass(frozen=True)
class McpCallOutcome:
    """One MCP request as the transport saw it."""

    elapsed_ms: float
    http_status: int | None = None
    error_class: ErrorClass | None = None
    is_error: bool = False
    # ``result.isError``: the tool ran and failed. Distinct from a transport
    # failure, and the signal that a fixture or a manifest drifted.
    tool_is_error: bool = False
    body_head: str = ""
    session_id: str | None = None
    reinitialised: bool = False
    jsonrpc_error: str | None = None


@dataclass
class TransportStats:
    """Generator-side surcharges, reported separately from SUT latency."""

    calls: int = 0
    failures: int = 0
    reinitialisations: int = 0
    retries: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot(self) -> dict[str, int]:
        with self.lock:
            return {
                "calls": self.calls,
                "failures": self.failures,
                "reinitialisations": self.reinitialisations,
                "retries": self.retries,
            }


def parse_mcp_response(content_type: str, raw: bytes) -> dict[str, Any] | None:
    """Extract the JSON-RPC message from a JSON or SSE body.

    Returns ``None`` for an empty body, which is the correct answer to a
    notification and therefore not an error.
    """
    if not raw.strip():
        return None
    text = raw.decode("utf-8", errors="replace")
    if "text/event-stream" in content_type or text.lstrip().startswith("event:"):
        return _parse_sse(text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"unparseable MCP body: {exc}") from exc
    return parsed if isinstance(parsed, dict) else None


def _parse_sse(text: str) -> dict[str, Any] | None:
    """Return the last SSE frame carrying a JSON-RPC object.

    A stream may hold several frames (progress notifications before the
    response, or the response itself only), so the last parseable frame wins
    rather than the first.
    """
    message: dict[str, Any] | None = None
    data_lines: list[str] = []

    def flush() -> None:
        nonlocal message
        if not data_lines:
            return
        try:
            parsed = json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            return
        if isinstance(parsed, dict):
            message = parsed

    for line in text.splitlines():
        if line.startswith("data:"):
            data_lines.append(line[len("data:") :].lstrip())
        elif not line.strip():
            flush()
            data_lines = []
    flush()
    return message


def classify_transport_error(exc: BaseException) -> ErrorClass:
    """Map a client-side exception to a structural error class."""
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return ErrorClass.TIMEOUT_CLIENT
    if isinstance(
        exc,
        (
            ConnectionResetError,
            ConnectionAbortedError,
            BrokenPipeError,
            http.client.RemoteDisconnected,
            http.client.IncompleteRead,
        ),
    ):
        return ErrorClass.RESET
    if isinstance(exc, (ConnectionError, http.client.HTTPException)):
        return ErrorClass.RESET
    return ErrorClass.OTHER


def classify_http_status(status: int) -> ErrorClass | None:
    """Structural class of a non-2xx status, or ``None`` when it is a 2xx."""
    if 200 <= status < 300:
        return None
    if status == 429:
        return ErrorClass.BUDGET_429
    if status in (408, 504):
        return ErrorClass.TIMEOUT_SERVER
    if 500 <= status < 600:
        return ErrorClass.SERVER_5XX
    return None


class McpSession:
    """One Streamable HTTP MCP session for one tenant over one connection."""

    def __init__(
        self,
        tenant: str,
        connection: HttpConnectionLike,
        path: str,
        *,
        timeout_s: float,
        initialised: bool,
        session_id: str | None = None,
    ) -> None:
        self.tenant = tenant
        self.connection = connection
        self.path = path
        self.timeout_s = timeout_s
        self.session_id = session_id
        self.initialised = initialised
        self.request_id = 0
        # Set by the transport when a call had to be replayed on a fresh
        # session; the driver turns it into a record field.
        self.reinitialised_last_call = False

    def next_request_id(self) -> int:
        self.request_id += 1
        return self.request_id


class McpTransport:
    """Streamable HTTP client: handshake, tool calls, manifest, reinit."""

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        timeout_s: float = 30.0,
        connection_factory: ConnectionFactory | None = None,
    ) -> None:
        parts = urlsplit(base_url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError(f"base_url must be absolute http(s), got {base_url!r}")
        self.scheme = parts.scheme
        self.host = parts.hostname
        self.port = parts.port or (443 if parts.scheme == "https" else 80)
        self.path = parts.path or "/mcp"
        self.query = f"?{parts.query}" if parts.query else ""
        self.api_key = api_key
        self.timeout_s = timeout_s
        self._factory = connection_factory or _default_connection_factory
        self.stats = TransportStats()

    # ── sessions ───────────────────────────────────────────────────────────

    def open(self, tenant: str) -> McpSession:
        """Open a session and complete the MCP handshake.

        The handshake is included in the stage's warm-up, not in its statistics:
        it is a cold start, and §3 keeps cold starts out of capacity profiles.
        """
        connection = self._factory(self.scheme, self.host, self.port, self.timeout_s)
        session = McpSession(
            tenant,
            connection,
            self.path,
            timeout_s=self.timeout_s,
            initialised=False,
        )
        self._initialise(session)
        return session

    def _initialise(self, session: McpSession) -> None:
        body = {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
            },
        }
        status, headers, raw, _ = self._post(session, body)
        if not 200 <= status < 300:
            raise TransportError(
                f"MCP initialize failed: HTTP {status}", classify_http_status(status)
            )
        session.session_id = _header(headers, SESSION_HEADER)
        session.initialised = True
        self._post(
            session,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            notify=True,
        )

    def close(self, session: McpSession) -> None:
        try:
            session.connection.close()
        except Exception:  # closing is best effort; nothing to report
            pass
        session.initialised = False

    # ── calls ──────────────────────────────────────────────────────────────

    def call_tool(
        self, session: McpSession, tool: str, arguments: dict[str, Any]
    ) -> McpCallOutcome:
        """Call one tool, replaying once on a fresh session if the server forgot it."""
        session.reinitialised_last_call = False
        started = time.perf_counter()
        outcome = self._call_tool_once(session, tool, arguments)
        if outcome.error_class is ErrorClass.OTHER and outcome.http_status == 404:
            # mcp-go answers an unknown session with 404. That is a driver
            # concern, not a platform failure: forget the id, handshake again and
            # replay once. The replay is counted, never hidden.
            with self.stats.lock:
                self.stats.reinitialisations += 1
                self.stats.retries += 1
            self.close(session)
            session.connection = self._factory(
                self.scheme, self.host, self.port, self.timeout_s
            )
            self._initialise(session)
            session.reinitialised_last_call = True
            outcome = self._call_tool_once(session, tool, arguments)
            outcome = replace(
                outcome,
                elapsed_ms=(time.perf_counter() - started) * 1000.0,
                reinitialised=True,
            )
        with self.stats.lock:
            self.stats.calls += 1
            if outcome.is_error:
                self.stats.failures += 1
        return outcome

    def _call_tool_once(
        self, session: McpSession, tool: str, arguments: dict[str, Any]
    ) -> McpCallOutcome:
        body = {
            "jsonrpc": "2.0",
            "id": session.next_request_id(),
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        }
        started = time.perf_counter()
        try:
            status, _headers, raw, content_type = self._post(session, body)
        except TransportError as exc:
            return McpCallOutcome(
                elapsed_ms=(time.perf_counter() - started) * 1000.0,
                error_class=exc.error_class,
                is_error=True,
            )
        elapsed = (time.perf_counter() - started) * 1000.0

        status_class = classify_http_status(status)
        if status_class is not None:
            return McpCallOutcome(
                elapsed_ms=elapsed,
                http_status=status,
                error_class=status_class,
                is_error=True,
                body_head=_sanitise(raw, content_type),
                session_id=session.session_id,
            )
        if not 200 <= status < 300:
            # 4xx that is not a known budget or timeout: request-level rejection.
            return McpCallOutcome(
                elapsed_ms=elapsed,
                http_status=status,
                error_class=ErrorClass.OTHER,
                is_error=True,
                body_head=_sanitise(raw, content_type),
                session_id=session.session_id,
            )

        try:
            message = parse_mcp_response(content_type, raw)
        except ValueError as exc:
            return McpCallOutcome(
                elapsed_ms=elapsed,
                http_status=status,
                error_class=ErrorClass.SSE_ABORT,
                is_error=True,
                body_head=str(exc)[:BODY_HEAD_LIMIT],
                session_id=session.session_id,
            )
        if message is None:
            return McpCallOutcome(
                elapsed_ms=elapsed,
                http_status=status,
                error_class=ErrorClass.SSE_ABORT,
                is_error=True,
                body_head="empty MCP response body",
                session_id=session.session_id,
            )

        error = message.get("error")
        if isinstance(error, dict):
            return McpCallOutcome(
                elapsed_ms=elapsed,
                http_status=status,
                error_class=ErrorClass.OTHER,
                is_error=True,
                jsonrpc_error=str(error.get("code", "jsonrpc_error")),
                body_head=str(error.get("message", ""))[:BODY_HEAD_LIMIT],
                session_id=session.session_id,
            )

        result = message.get("result")
        tool_is_error = bool(isinstance(result, dict) and result.get("isError"))
        return McpCallOutcome(
            elapsed_ms=elapsed,
            http_status=status,
            error_class=ErrorClass.OTHER if tool_is_error else None,
            is_error=tool_is_error,
            tool_is_error=tool_is_error,
            body_head=_sanitise(raw, content_type) if tool_is_error else "",
            session_id=session.session_id,
        )

    # ── manifest ───────────────────────────────────────────────────────────

    def fetch_manifest(self, tenant: str) -> list[str]:
        """Live tool names for a tenant, for profile validation (§12.1).

        The manifest is a plain GET served by the gateway, not an MCP method:
        it is the tenant config, and the tool names in it are the ground truth a
        profile is checked against.
        """
        connection = self._factory(self.scheme, self.host, self.port, self.timeout_s)
        try:
            connection.request(
                "GET",
                "/mcp/manifest",
                None,
                self._headers(tenant),
            )
            response = connection.getresponse()
            raw = response.read()
            if not 200 <= response.status < 300:
                raise TransportError(
                    f"manifest fetch failed: HTTP {response.status}",
                    classify_http_status(response.status),
                )
        finally:
            connection.close()
        payload = json.loads(raw.decode("utf-8"))
        tools = payload.get("mcp_tools", payload.get("tools", []))
        return [str(entry.get("name", "")) for entry in tools if entry.get("name")]

    # ── plumbing ───────────────────────────────────────────────────────────

    def _headers(
        self, tenant: str, *, session: McpSession | None = None
    ) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": f"{CLIENT_NAME}/{CLIENT_VERSION}",
            "X-Tenant-ID": tenant,
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if session is not None and session.session_id:
            headers[SESSION_HEADER] = session.session_id
        return headers

    def _post(
        self, session: McpSession, body: dict[str, Any], *, notify: bool = False
    ) -> tuple[int, Any, bytes, str]:
        url = f"{self.path}{self.query}"
        payload = json.dumps(body).encode()
        try:
            session.connection.request(
                "POST", url, payload, self._headers(session.tenant, session=session)
            )
            response = session.connection.getresponse()
            raw = response.read()
        except Exception as exc:
            raise TransportError(str(exc), classify_transport_error(exc)) from exc
        content_type = _header(response.headers, "Content-Type") or ""
        return response.status, response.headers, raw, content_type


class TransportError(Exception):
    """A client-side failure, already mapped to a structural error class."""

    def __init__(self, message: str, error_class: ErrorClass | None = None) -> None:
        super().__init__(message)
        self.error_class = error_class or ErrorClass.OTHER


def _header(headers: Any, name: str) -> str | None:
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if getter is not None:
        value = getter(name)
        return str(value) if value is not None else None
    return None


def _sanitise(raw: bytes, content_type: str) -> str:
    """Truncated failure excerpt: no DSN, no credentials, no stack traces.

    Callers pass this only for failures; a successful tool payload is never
    excerpted because it contains tenant rows.
    """
    if not raw:
        return ""
    try:
        message = parse_mcp_response(content_type, raw)
    except ValueError:
        message = None
    text: str
    if isinstance(message, dict):
        result = message.get("result")
        if isinstance(result, dict) and result.get("content"):
            parts = [
                str(item.get("text", ""))
                for item in result["content"]
                if isinstance(item, dict)
            ]
            text = " ".join(parts)
        else:
            text = json.dumps(message, ensure_ascii=False)
    else:
        text = raw.decode("utf-8", errors="replace")
    return " ".join(text.split())[:BODY_HEAD_LIMIT]
