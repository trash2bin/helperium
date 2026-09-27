"""Transport contract tests (doc/stress/README.md §7, §10, §11).

The connection is injected, so every input that matters - status codes, body
framing, a dropped session, a socket timeout - is produced without a live stack.
What these tests protect: a failure is classified structurally rather than by
message text, a successful tool payload never leaves the process, and a server
that forgot our session costs one replay instead of a stage full of errors.
"""

from __future__ import annotations

import json
import socket
from typing import Any

import pytest

from agent_db.stress.records import ErrorClass
from agent_db.stress.transport import (
    McpTransport,
    TransportError,
    classify_http_status,
    classify_transport_error,
    parse_mcp_response,
)


class FakeResponse:
    def __init__(
        self, status: int, body: str = "", headers: dict[str, str] | None = None
    ):
        self.status = status
        self.headers = headers or {}
        self._body = body.encode()

    def read(self) -> bytes:
        return self._body


class FakeServer:
    """Scripted HTTP endpoint: one response per request, in order.

    A script entry may be a :class:`FakeResponse` or an exception instance to
    raise from the socket layer.
    """

    def __init__(self, *script: Any) -> None:
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []
        self.connections = 0
        self.closed = 0

    def factory(
        self, scheme: str, host: str, port: int, timeout: float
    ) -> "FakeConnection":
        self.connections += 1
        return FakeConnection(self)

    def push(self, *responses: Any) -> None:
        self.script.extend(responses)


class FakeConnection:
    def __init__(self, server: FakeServer) -> None:
        self.server = server
        self._response: FakeResponse | None = None

    def request(
        self,
        method: str,
        url: str,
        body: bytes | None = None,
        headers: dict | None = None,
    ) -> None:
        self.server.requests.append(
            {
                "method": method,
                "url": url,
                "body": json.loads(body) if body else None,
                "headers": dict(headers or {}),
            }
        )
        if not self.server.script:
            raise AssertionError(f"unexpected request: {method} {url}")
        item = self.server.script.pop(0)
        if isinstance(item, BaseException):
            self._response = None
            raise item
        self._response = item

    def getresponse(self) -> FakeResponse:
        assert self._response is not None
        return self._response

    def close(self) -> None:
        self.server.closed += 1


def jsonrpc_result(payload: dict[str, Any]) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": 1, "result": payload})


def tool_ok(text: str = '{"ok":true}') -> FakeResponse:
    return FakeResponse(
        200,
        jsonrpc_result({"content": [{"type": "text", "text": text}], "isError": False}),
        {"Content-Type": "application/json"},
    )


def tool_error(text: str = "entity not found") -> FakeResponse:
    return FakeResponse(
        200,
        jsonrpc_result({"content": [{"type": "text", "text": text}], "isError": True}),
        {"Content-Type": "application/json"},
    )


def initialise(session_id: str = "sess-1") -> list[FakeResponse]:
    return [
        FakeResponse(
            200,
            "{}",
            {"Mcp-Session-Id": session_id, "Content-Type": "application/json"},
        ),
        FakeResponse(202, "", {}),
    ]


def make_transport(server: FakeServer, **kwargs: Any) -> McpTransport:
    return McpTransport(
        "http://127.0.0.1:18083/mcp",
        connection_factory=server.factory,
        **kwargs,
    )


class TestResponseParsing:
    def test_json_body(self) -> None:
        message = parse_mcp_response(
            "application/json", b'{"jsonrpc":"2.0","id":1,"result":{}}'
        )
        assert message is not None and "result" in message

    def test_sse_body(self) -> None:
        body = b'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"isError":false}}\n\n'
        message = parse_mcp_response("text/event-stream", body)
        assert message == {"jsonrpc": "2.0", "id": 1, "result": {"isError": False}}

    def test_last_frame_wins_over_progress_notifications(self) -> None:
        # mcp-go may emit progress notifications before the response; the
        # response is the frame that carries the id.
        body = (
            b'event: message\ndata: {"jsonrpc":"2.0","method":"notifications/progress"}\n\n'
            b'event: message\ndata: {"jsonrpc":"2.0","id":7,"result":{"ok":1}}\n\n'
        )
        message = parse_mcp_response("text/event-stream", body)
        assert message == {"jsonrpc": "2.0", "id": 7, "result": {"ok": 1}}

    def test_multi_line_data_frame(self) -> None:
        body = b'data: {"jsonrpc":"2.0",\ndata: "id":3,"result":{}}\n\n'
        message = parse_mcp_response("text/event-stream", body)
        assert message is not None and message.get("id") == 3

    def test_empty_body_is_not_an_error(self) -> None:
        assert parse_mcp_response("application/json", b"") is None

    def test_unparseable_body_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_mcp_response("application/json", b"<html>gateway</html>")


class TestClassification:
    def test_status_classes(self) -> None:
        assert classify_http_status(200) is None
        assert classify_http_status(429) is ErrorClass.BUDGET_429
        assert classify_http_status(504) is ErrorClass.TIMEOUT_SERVER
        assert classify_http_status(408) is ErrorClass.TIMEOUT_SERVER
        assert classify_http_status(503) is ErrorClass.SERVER_5XX
        assert classify_http_status(400) is None

    def test_exception_classes(self) -> None:
        assert classify_transport_error(socket.timeout()) is ErrorClass.TIMEOUT_CLIENT
        assert classify_transport_error(TimeoutError()) is ErrorClass.TIMEOUT_CLIENT
        assert classify_transport_error(ConnectionResetError()) is ErrorClass.RESET
        assert classify_transport_error(BrokenPipeError()) is ErrorClass.RESET
        assert classify_transport_error(ValueError("odd")) is ErrorClass.OTHER


class TestSessionHandshake:
    def test_initialize_captures_session_and_sends_notification(self) -> None:
        server = FakeServer(*initialise("sess-42"), tool_ok())
        transport = make_transport(server, api_key="k")
        session = transport.open("t-1")
        assert session.session_id == "sess-42"
        transport.call_tool(session, "db_get", {"entity": "group"})

        init, notification, call = server.requests
        assert init["body"]["method"] == "initialize"
        assert init["headers"]["X-Tenant-ID"] == "t-1"
        assert init["headers"]["Authorization"] == "Bearer k"
        assert "Mcp-Session-Id" not in init["headers"]
        assert notification["body"]["method"] == "notifications/initialized"
        assert notification["headers"]["Mcp-Session-Id"] == "sess-42"
        assert call["body"]["params"] == {
            "name": "db_get",
            "arguments": {"entity": "group"},
        }
        assert call["headers"]["Mcp-Session-Id"] == "sess-42"

    def test_handshake_failure_is_typed(self) -> None:
        server = FakeServer(FakeResponse(503, "down"))
        transport = make_transport(server)
        with pytest.raises(TransportError) as excinfo:
            transport.open("t-1")
        assert excinfo.value.error_class is ErrorClass.SERVER_5XX

    def test_relative_base_url_is_refused(self) -> None:
        with pytest.raises(ValueError):
            McpTransport("localhost:8083/mcp")


class TestToolCall:
    def test_successful_call(self) -> None:
        server = FakeServer(*initialise(), tool_ok())
        transport = make_transport(server)
        session = transport.open("t-1")
        outcome = transport.call_tool(session, "db_map", {})
        assert outcome.is_error is False
        assert outcome.http_status == 200
        assert outcome.error_class is None
        assert outcome.elapsed_ms > 0
        assert transport.stats.snapshot()["calls"] == 1

    def test_successful_payload_is_not_excerpted(self) -> None:
        # A tool result carries tenant rows; only failures may keep an excerpt.
        server = FakeServer(*initialise(), tool_ok('{"full_name":"Иван Петров"}'))
        transport = make_transport(server)
        session = transport.open("t-1")
        outcome = transport.call_tool(session, "db_get", {})
        assert outcome.body_head == ""

    def test_tool_level_error_is_distinguished_from_transport_success(self) -> None:
        server = FakeServer(*initialise(), tool_error("unknown relation"))
        transport = make_transport(server)
        session = transport.open("t-1")
        outcome = transport.call_tool(session, "db_related", {})
        assert outcome.is_error is True
        assert outcome.tool_is_error is True
        assert outcome.http_status == 200
        assert outcome.error_class is ErrorClass.OTHER
        assert "unknown relation" in outcome.body_head
        assert transport.stats.snapshot()["failures"] == 1

    def test_budget_status_maps_to_429(self) -> None:
        server = FakeServer(
            *initialise(), FakeResponse(429, '{"error":"rate limited"}')
        )
        transport = make_transport(server)
        session = transport.open("t-1")
        outcome = transport.call_tool(session, "db_get", {})
        assert outcome.error_class is ErrorClass.BUDGET_429
        assert outcome.http_status == 429

    def test_server_timeout_is_distinguishable_from_client_timeout(self) -> None:
        server = FakeServer(*initialise(), FakeResponse(504, "upstream timeout"))
        transport = make_transport(server)
        session = transport.open("t-1")
        outcome = transport.call_tool(session, "db_get", {})
        assert outcome.error_class is ErrorClass.TIMEOUT_SERVER

    def test_client_timeout(self) -> None:
        server = FakeServer(*initialise(), socket.timeout("read timed out"))
        transport = make_transport(server)
        session = transport.open("t-1")
        outcome = transport.call_tool(session, "db_get", {})
        assert outcome.error_class is ErrorClass.TIMEOUT_CLIENT
        assert outcome.http_status is None

    def test_connection_reset(self) -> None:
        server = FakeServer(*initialise(), ConnectionResetError("peer gone"))
        transport = make_transport(server)
        session = transport.open("t-1")
        outcome = transport.call_tool(session, "db_get", {})
        assert outcome.error_class is ErrorClass.RESET

    def test_jsonrpc_error_envelope(self) -> None:
        server = FakeServer(
            *initialise(),
            FakeResponse(
                200,
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "error": {"code": -32602, "message": "bad"},
                    }
                ),
                {"Content-Type": "application/json"},
            ),
        )
        transport = make_transport(server)
        session = transport.open("t-1")
        outcome = transport.call_tool(session, "db_get", {})
        assert outcome.is_error is True
        assert outcome.tool_is_error is False
        assert outcome.jsonrpc_error == "-32602"

    def test_empty_body_on_a_request_is_reported(self) -> None:
        server = FakeServer(
            *initialise(), FakeResponse(200, "", {"Content-Type": "application/json"})
        )
        transport = make_transport(server)
        session = transport.open("t-1")
        outcome = transport.call_tool(session, "db_get", {})
        assert outcome.error_class is ErrorClass.SSE_ABORT

    def test_sse_response_body(self) -> None:
        server = FakeServer(
            *initialise(),
            FakeResponse(
                200,
                'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"isError":false}}\n\n',
                {"Content-Type": "text/event-stream"},
            ),
        )
        transport = make_transport(server)
        session = transport.open("t-1")
        outcome = transport.call_tool(session, "db_get", {})
        assert outcome.is_error is False


class TestSessionExpiry:
    def test_unknown_session_is_replayed_once_on_a_fresh_session(self) -> None:
        server = FakeServer(
            *initialise("sess-old"),
            FakeResponse(404, "Invalid session ID"),
            *initialise("sess-new"),
            tool_ok(),
        )
        transport = make_transport(server)
        session = transport.open("t-1")
        outcome = transport.call_tool(session, "db_get", {"entity": "group"})

        assert outcome.is_error is False
        assert outcome.reinitialised is True
        assert session.session_id == "sess-new"
        stats = transport.stats.snapshot()
        assert stats["reinitialisations"] == 1
        assert stats["retries"] == 1
        # The replay must carry the new session id, and the whole call -
        # handshake included - is what the driver reports as its latency.
        assert server.requests[-1]["headers"]["Mcp-Session-Id"] == "sess-new"
        assert outcome.elapsed_ms > 0

    def test_replay_failure_is_reported_not_retried_forever(self) -> None:
        server = FakeServer(
            *initialise("sess-old"),
            FakeResponse(404, "Invalid session ID"),
            *initialise("sess-new"),
            FakeResponse(500, "boom"),
        )
        transport = make_transport(server)
        session = transport.open("t-1")
        outcome = transport.call_tool(session, "db_get", {})
        assert outcome.error_class is ErrorClass.SERVER_5XX
        assert outcome.reinitialised is True
        assert transport.stats.snapshot()["retries"] == 1


class TestManifest:
    def test_manifest_returns_tool_names(self) -> None:
        server = FakeServer(
            FakeResponse(
                200,
                json.dumps(
                    {
                        "mcp_tools": [
                            {"name": "db_map", "description": "schema"},
                            {"name": "db_get"},
                        ]
                    }
                ),
                {"Content-Type": "application/json"},
            )
        )
        transport = make_transport(server)
        assert transport.fetch_manifest("t-1") == ["db_map", "db_get"]
        request = server.requests[0]
        assert request["method"] == "GET"
        assert request["url"] == "/mcp/manifest"
        assert request["headers"]["X-Tenant-ID"] == "t-1"

    def test_manifest_failure_is_typed(self) -> None:
        server = FakeServer(FakeResponse(404, "no tenant"))
        transport = make_transport(server)
        with pytest.raises(TransportError):
            transport.fetch_manifest("ghost")
