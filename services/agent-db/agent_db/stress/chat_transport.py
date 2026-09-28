"""Chat transport: one Streamable-HTTP SSE session against api-service (§1, §3).

The L2/L3 unit of load is a *turn*: ``POST /api/chat`` with a message and a
session id, answered by an SSE stream whose first meaningful event is what the
widget renders. The transport's whole job is to expose that stream with the
timings §1 names (``ttfe_session``, ``ttfe``, ``ttfe_tool``, ``t_complete``) and
to keep the session contract the platform enforces:

- the first turn of a session mints a capability token, delivered both as the
  ``session`` event and the ``X-Session-Token`` header; **later turns on that
  session must present it** or are rejected with 401 before the abuse gate;
- the user-agent is part of the anti-abuse key (§5), so it is fixed here and
  recorded by the manifest rather than left to the HTTP library's default;
- quality refusals arrive as **HTTP 200 + SSE ``error``** (§5), so "error" means
  the stream said so, not that the transport threw.
"""

from __future__ import annotations

import http.client
import json
import socket
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Iterator

from .records import ErrorClass

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36 HelperiumStress/1.0"
)

# A browser-like accept header: the widget is the client this endpoint is built
# for, and the platform keys part of its abuse state off the client it sees.
ACCEPT_HEADER = "text/event-stream"


class ChatTransportError(Exception):
    """A client-side failure, already mapped to a structural error class."""

    def __init__(
        self,
        message: str,
        error_class: ErrorClass | None = None,
        status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.error_class = error_class
        self.status = status


@dataclass
class ChatSession:
    """One conversation the generator owns for the length of a stage."""

    tenant: str
    session_id: str
    connection: Any = None
    # Minted by the first turn's `session` event; every later turn presents it.
    session_token: str | None = None
    turns: int = 0
    reinitialised_last_turn: bool = False

    @property
    def key(self) -> str:
        return self.session_id


@dataclass(frozen=True)
class ChatFrame:
    """One parsed SSE event plus the offsets §1's metrics are made of."""

    event: dict[str, Any]
    # Milliseconds from the request being sent.
    offset_ms: float
    raw: str = ""

    @property
    def type(self) -> str:
        value = self.event.get("type")
        return value if isinstance(value, str) else ""


def classify_chat_status(status: int) -> ErrorClass | None:
    """Structural class of a non-2xx response, or ``None`` for a 2xx."""
    if 200 <= status < 300:
        return None
    if status == 429:
        return ErrorClass.BUDGET_429
    if status in (401, 403):
        # A session/capability-token the server rejected: structural (the
        # harness lost or never received the token), not message-derived.
        return ErrorClass.AUTH_401
    if status in (408, 504):
        return ErrorClass.TIMEOUT_SERVER
    if 500 <= status < 600:
        return ErrorClass.SERVER_5XX
    return None


def _classify_transport_error(exc: BaseException) -> ErrorClass:
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return ErrorClass.TIMEOUT_CLIENT
    if isinstance(exc, (ConnectionError, http.client.HTTPException)):
        return ErrorClass.RESET
    return ErrorClass.OTHER


def default_connection_factory(
    scheme: str, host: str, port: int, timeout: float
) -> http.client.HTTPConnection:
    if scheme == "https":
        return http.client.HTTPSConnection(host, port, timeout=timeout)
    return http.client.HTTPConnection(host, port, timeout=timeout)


ConnectionFactory = Callable[[str, str, int, float], http.client.HTTPConnection]


class ChatTransport:
    """SSE client for ``/api/chat`` (or ``/api/chat/{name}``) over one connection."""

    def __init__(
        self,
        base_url: str,
        *,
        agent: str | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout_s: float = 120.0,
        connection_factory: ConnectionFactory | None = None,
    ) -> None:
        from urllib.parse import urlsplit

        parts = urlsplit(base_url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError(f"base_url must be absolute http(s), got {base_url!r}")
        self.scheme = parts.scheme
        self.host = parts.hostname
        self.port = parts.port or (443 if parts.scheme == "https" else 80)
        self.base_path = parts.path.rstrip("/")
        self.agent = agent or None
        self.user_agent = user_agent
        self.timeout_s = timeout_s
        self._factory = connection_factory or default_connection_factory
        self.calls = 0
        self.failures = 0

    # ── sessions ───────────────────────────────────────────────────────────

    def open_session(self, tenant: str) -> ChatSession:
        """Create a session id and its connection. Cold start, so warm-up."""
        session = ChatSession(
            tenant=tenant,
            session_id=f"stress-{uuid.uuid4().hex[:12]}",
            connection=self._factory(self.scheme, self.host, self.port, self.timeout_s),
        )
        return session

    def close_session(self, session: ChatSession) -> None:
        try:
            session.connection.close()
        except Exception:  # closing is best effort; nothing to report
            pass

    # ── turns ──────────────────────────────────────────────────────────────

    def stream_turn(
        self,
        session: ChatSession,
        message: str,
        *,
        correlation_id: str,
    ) -> Iterator[ChatFrame]:
        """POST one turn and yield parsed SSE frames as they arrive.

        The response line protocol is api-service's: every event is a single
        ``data: {json}`` frame, and the terminal event is ``done``. The generator
        owns reconnection, so a dead stream raises instead of being retried here.
        """
        path = (
            f"{self.base_path}/api/chat/{self.agent}"
            if self.agent
            else f"{self.base_path}/api/chat"
        )
        payload = json.dumps(
            {"message": message, "session_id": session.session_id}
        ).encode()
        headers = {
            "Content-Type": "application/json",
            "Accept": ACCEPT_HEADER,
            "User-Agent": self.user_agent,
            "Accept-Language": "en",
            "X-Correlation-ID": correlation_id,
        }
        if session.session_token:
            headers["X-Session-Token"] = session.session_token

        started = time.perf_counter()
        try:
            session.connection.request("POST", path, payload, headers)
            response = session.connection.getresponse()
        except Exception as exc:
            raise ChatTransportError(str(exc), _classify_transport_error(exc)) from exc

        status = response.status
        if status != 200:
            body = b""
            try:
                body = response.read()
            except Exception:  # noqa: BLE001 - the status is the evidence
                pass
            self.failures += 1
            raise ChatTransportError(
                f"chat turn failed: HTTP {status}: {body[:200]!r}",
                classify_chat_status(status),
                status=status,
            )

        session.turns += 1
        self.calls += 1
        response_body_done = False
        try:
            for raw_line in response:
                line = (
                    raw_line.decode("utf-8", errors="replace")
                    if isinstance(raw_line, bytes)
                    else raw_line
                )
                if not line.startswith("data: "):
                    continue
                offset_ms = (time.perf_counter() - started) * 1000.0
                try:
                    event = json.loads(line[len("data: ") :])
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                frame = ChatFrame(event=event, offset_ms=offset_ms, raw=line.strip())
                if frame.type == "session" and not session.session_token:
                    token = event.get("session_token")
                    session.session_token = token if isinstance(token, str) else None
                yield frame
                if frame.type == "done":
                    return
            response_body_done = True
        except Exception as exc:
            raise ChatTransportError(str(exc), _classify_transport_error(exc)) from exc
        finally:
            if not response_body_done:
                # The consumer left early (the terminal frame arrived, or a turn
                # timeout). The connection is only reusable once the body has
                # been read to its declared length, so drain what is buffered:
                # the stream is short (one turn's events), and a stale socket
                # would poison the next turn of this session.
                try:
                    response.read()
                except Exception:  # noqa: BLE001 - draining is best effort
                    pass


__all__ = [
    "ACCEPT_HEADER",
    "DEFAULT_USER_AGENT",
    "ChatFrame",
    "ChatSession",
    "ChatTransport",
    "ChatTransportError",
    "classify_chat_status",
]
