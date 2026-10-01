"""Chat transport and L2 driver contract tests (§1, §3, §5, §7).

The chat path is where the platform's own work lives (SSE framing, the session
store, the per-tenant MCP client), so what is pinned here is the session
contract the platform enforces - the capability token a later turn must present,
the 200 + SSE ``error`` shape of a quality refusal - and the timings §1 named.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from agent_db.stress.chat_driver import ChatDriver
from agent_db.stress.chat_transport import (
    ChatFrame,
    ChatSession,
    ChatTransport,
    ChatTransportError,
    classify_chat_status,
)
from agent_db.stress.records import ErrorClass
from agent_db.stress.runner import WorkloadStep


def _sse(*events: dict[str, Any]) -> bytes:
    return b"".join(
        f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode() for event in events
    )


@dataclass
class RecordedRequest:
    path: str
    headers: dict[str, str]
    body: dict[str, Any]


class FakeChatStand:
    """A scriptable api-service: SSE frames per turn, requests remembered."""

    def __init__(
        self,
        *,
        script: list[list[dict[str, Any]]] | None = None,
        status: int = 200,
    ) -> None:
        # Turn N of any session is answered with script[N % len(script)].
        self.script = script or [
            [
                {"type": "session", "session_id": "s", "session_token": "tok-1"},
                {
                    "type": "tool_call",
                    "id": "call-1",
                    "name": "db_get",
                    "arguments": {},
                },
                {"type": "tool_result", "name": "db_get", "result": "ok"},
                {"type": "final", "content": "готово"},
                {"type": "done"},
            ]
        ]
        self.status = status
        self.requests: list[RecordedRequest] = []
        self._lock = threading.Lock()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ── server lifecycle ───────────────────────────────────────────────────

    def start(self) -> FakeChatStand:
        stand = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:
                pass

            def do_GET(self) -> None:  # noqa: N802
                body = b'{"status": "ok"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length)) if length else {}
                record = RecordedRequest(
                    path=self.path,
                    headers={
                        key: value
                        for key, value in self.headers.items()
                        if key.lower()
                        in {"x-session-token", "user-agent", "x-correlation-id"}
                    },
                    body=payload,
                )
                with stand._lock:
                    stand.requests.append(record)
                turn = len(stand.requests) - 1
                events = stand.script[turn % len(stand.script)]
                body = _sse(*events)
                self.send_response(stand.status)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                if turn == 0:
                    self.send_header("X-Session-Token", "tok-1")
                self.end_headers()
                self.wfile.write(body)

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    @property
    def base_url(self) -> str:
        assert self._httpd is not None
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"


@pytest.fixture
def stand():
    instance = FakeChatStand().start()
    yield instance
    instance.stop()


def _transport(stand: FakeChatStand) -> ChatTransport:
    return ChatTransport(stand.base_url, timeout_s=10.0)


def _step(name: str = "one_tool", **overrides: Any) -> WorkloadStep:
    payload: dict[str, Any] = {
        "weight": 1.0,
        "name": name,
        "tools": ["db_get"],
        "history_turns": 0,
        "repeat": {},
    }
    payload.update(overrides)
    return WorkloadStep.model_validate(payload)


class TestSessionCapability:
    def test_the_first_turn_receives_and_stores_the_token(self, stand):
        transport = _transport(stand)
        session = transport.open_session("t-1")
        frames = list(
            transport.stream_turn(session, "привет", correlation_id="c-1")
        )
        assert session.session_token == "tok-1"
        assert frames[0].type == "session"
        assert frames[-1].type == "done"
        transport.close_session(session)

    def test_a_later_turn_presents_the_token_it_was_given(self, stand):
        transport = _transport(stand)
        session = transport.open_session("t-1")
        list(transport.stream_turn(session, "первый", correlation_id="c-1"))
        list(transport.stream_turn(session, "второй", correlation_id="c-2"))
        assert stand.requests[1].headers["X-Session-Token"] == "tok-1"
        # The same session id is reused, because history is the point (§3).
        assert stand.requests[1].body["session_id"] == stand.requests[0].body["session_id"]
        transport.close_session(session)

    def test_the_user_agent_is_the_recorded_browser_like_one(self, stand):
        transport = _transport(stand)
        session = transport.open_session("t-1")
        list(transport.stream_turn(session, "привет", correlation_id="c-1"))
        ua = stand.requests[0].headers["User-Agent"]
        # §5: the platform blocks bare HTTP clients by UA substring; the
        # generator must look like the client this endpoint is built for.
        assert "Mozilla/5.0" in ua
        assert "python" not in ua.lower()
        transport.close_session(session)


class TestStatusClassification:
    def test_structural_classes_come_from_the_status(self):
        assert classify_chat_status(200) is None
        assert classify_chat_status(429) is ErrorClass.BUDGET_429
        assert classify_chat_status(504) is ErrorClass.TIMEOUT_SERVER
        assert classify_chat_status(500) is ErrorClass.SERVER_5XX
        assert classify_chat_status(404) is None  # 4xx that is not a known budget

    def test_a_429_raises_instead_of_yielding_frames(self):
        stand = FakeChatStand(status=429).start()
        try:
            transport = _transport(stand)
            session = transport.open_session("t-1")
            with pytest.raises(ChatTransportError) as excinfo:
                list(
                    transport.stream_turn(session, "привет", correlation_id="c-1")
                )
            assert excinfo.value.error_class is ErrorClass.BUDGET_429
        finally:
            stand.stop()


class _ScriptedChatTransport:
    """A transport whose frames carry chosen offsets, for deterministic timing."""

    def __init__(self, frames: list[tuple[str, float]]) -> None:
        self._frames = frames

    def open_session(self, tenant: str) -> ChatSession:  # noqa: ARG002
        return ChatSession(tenant=tenant, session_id="stress-scripted")

    def close_session(self, session: ChatSession) -> None:
        return

    def stream_turn(self, session, message, *, correlation_id):  # noqa: ANN001, ARG002
        for event_type, offset_ms in self._frames:
            event: dict[str, Any] = {"type": event_type}
            if event_type == "tool_call":
                event = {"type": "tool_call", "id": "c-1", "name": "db_get"}
            elif event_type == "tool_result":
                event = {"type": "tool_result", "name": "db_get", "result": "ok"}
            yield ChatFrame(event=event, offset_ms=offset_ms)


class TestChatDriver:
    def test_a_turn_records_the_documented_timings(self):
        transport = _ScriptedChatTransport(
            [
                ("session", 1.0),
                ("tool_call", 2.0),
                ("tool_result", 3.0),
                ("final", 3.5),
                ("done", 4.0),
            ]
        )
        driver = ChatDriver(transport)
        session = driver.open_session("t-1")
        execution = driver.execute_turn(
            session,
            _step(),
            planned_ms=0.0,
            started_ms=5.0,
            tenant="t-1",
            scenario="fixture",
        )
        record = execution.record
        assert record.ttfe_session == pytest.approx(1.0)
        # §1: ttfe skips the handshake event - the first *meaningful* frame is
        # what the widget renders, and it is measured from the same start.
        assert record.ttfe == pytest.approx(2.0)
        assert record.ttfe_tool == pytest.approx(2.0)
        assert record.t_complete == pytest.approx(4.0)
        assert record.actual_ms == pytest.approx(4.0)
        assert record.sse_terminal_event == "done"
        assert record.is_error is False
        assert record.session_id == "stress-scripted"
        # The wire's tool_result carries no id (api-service builds it from
        # name/display_name), so the join runs on the name: duration is the
        # result offset minus the call offset, 3.0 - 2.0.
        assert execution.calls[0].elapsed_ms == pytest.approx(1.0)
        driver.close_session(session)

    def test_a_transport_refusal_is_a_measurement_not_a_dead_worker(self):
        class Refusing:
            def open_session(self, tenant: str) -> ChatSession:
                return ChatSession(tenant=tenant, session_id="stress-refused")

            def close_session(self, session: ChatSession) -> None:
                return

            def stream_turn(self, session, message, *, correlation_id):
                raise ChatTransportError(
                    "chat turn failed: HTTP 429: b'{}'",
                    classify_chat_status(429),
                    status=429,
                )
                yield  # pragma: no cover - makes this a generator

        driver = ChatDriver(Refusing())
        session = driver.open_session("t-1")
        execution = driver.execute_turn(
            session,
            _step(),
            planned_ms=0.0,
            started_ms=0.0,
            tenant="t-1",
            scenario="fixture",
        )
        record = execution.record
        # §5: the 429 lives in the error mix. Like the L1 driver, the chat
        # driver records the refusal in-band instead of letting it kill the
        # worker thread - the rung stays judged, and the error rate shows it.
        assert record.is_error is True
        assert record.http_status == 429
        assert record.error_class == ErrorClass.BUDGET_429
        assert record.sse_terminal_event == "error"
        driver.close_session(session)

    def test_a_rejected_capability_token_has_its_own_class(self):
        assert classify_chat_status(401) == ErrorClass.AUTH_401
        assert classify_chat_status(403) == ErrorClass.AUTH_401
        driver = ChatDriver(_ScriptedChatTransport([("done", 0.5)]))
        session = driver.open_session("t-1")
        execution = driver.execute_turn(
            session,
            _step(),
            planned_ms=0.0,
            started_ms=0.0,
            tenant="t-1",
            scenario="fixture",
        )
        assert execution.record.error_class is None
        driver.close_session(session)

    def test_the_live_wire_produces_the_same_shape(self, stand):
        driver = ChatDriver(_transport(stand))
        session = driver.open_session("t-1")
        execution = driver.execute_turn(
            session,
            _step(),
            planned_ms=0.0,
            started_ms=5.0,
            tenant="t-1",
            scenario="fixture",
        )
        record = execution.record
        assert record.ttfe_session is not None
        assert record.ttfe is not None
        assert record.ttfe >= (record.ttfe_session or 0.0)
        assert record.t_complete is not None
        assert record.actual_ms == record.t_complete
        assert record.sse_terminal_event == "done"
        assert record.session_id.startswith("stress-")
        driver.close_session(session)

    def test_tool_calls_are_decomposed_next_to_the_turn(self, stand):
        driver = ChatDriver(_transport(stand))
        session = driver.open_session("t-1")
        execution = driver.execute_turn(
            session,
            _step(),
            planned_ms=0.0,
            started_ms=0.0,
            tenant="t-1",
            scenario="fixture",
        )
        # §10: the turn is the scheduling unit; the per-call decomposition has no
        # planned offset of its own and is written beside the record. One row per
        # call, with the call's own duration - not a second row per result.
        assert len(execution.calls) == 1
        assert execution.calls[0].tool == "db_get"
        assert execution.calls[0].elapsed_ms >= 0
        driver.close_session(session)

    def test_a_quality_refusal_is_an_error_with_a_named_terminal(self, stand):
        refusal = FakeChatStand(
            script=[[{"type": "error", "text": "Слишком часто."}, {"type": "done"}]]
        ).start()
        try:
            driver = ChatDriver(_transport(refusal))
            session = driver.open_session("t-1")
            execution = driver.execute_turn(
                session,
                _step(),
                planned_ms=0.0,
                started_ms=0.0,
                tenant="t-1",
                scenario="fixture",
            )
            record = execution.record
            # §5: a quality refusal is HTTP 200 + SSE error, not a 429 - so the
            # transport did not raise and the driver classifies the stream fact.
            assert record.is_error is True
            assert record.error_class is ErrorClass.SSE_ABORT
            assert record.sse_terminal_event == "error"
            assert record.http_status is None
        finally:
            refusal.stop()

    def test_a_stream_that_ends_without_done_is_an_abort(self):
        truncated = FakeChatStand(
            script=[[{"type": "session", "session_token": "tok"}]]
        ).start()
        try:
            driver = ChatDriver(_transport(truncated))
            session = driver.open_session("t-1")
            execution = driver.execute_turn(
                session,
                _step(),
                planned_ms=0.0,
                started_ms=0.0,
                tenant="t-1",
                scenario="fixture",
            )
            assert execution.record.sse_terminal_event == "aborted"
            assert execution.record.is_error is True
        finally:
            truncated.stop()

    def test_the_message_carries_a_stable_marker_for_the_stub_join(self, stand):
        driver = ChatDriver(_transport(stand))
        session = driver.open_session("t-1")
        first = driver.message_for(session, _step("search_get"))
        second = driver.message_for(session, _step("search_get"))
        assert "[stress:search_get:1]" in first
        assert "[stress:search_get:2]" in second
        # The marker is what a stub's timing log is joined on (§4), so it must be
        # unique per turn of a session.
        assert first != second
        driver.close_session(session)


class TestChatPreflightContract:
    """``chat_preflight`` probes ``transport.base_url + "/health"`` first.

    A live L2 run found that ``ChatTransport`` parsed its base URL into
    scheme/host/port and discarded the string, so the preflight - the very
    first step of any chat-layer run - died on an ``AttributeError`` before
    measuring anything. The origin is the contract between the two.
    """

    def test_the_transport_exposes_the_origin_the_preflight_probes(self):
        transport = ChatTransport("http://127.0.0.1:8081", timeout_s=10.0)
        assert transport.base_url == "http://127.0.0.1:8081"

    def test_a_chat_path_does_not_leak_into_the_probed_origin(self):
        # The health endpoint lives at the origin; a caller that hands over the
        # full /api/chat URL must not send the probe to /api/chat/health.
        transport = ChatTransport("http://127.0.0.1:8081/api/chat", timeout_s=10.0)
        assert transport.base_url == "http://127.0.0.1:8081"

    def test_the_health_probe_reaches_the_stand(self, stand):
        from agent_db.stress.chat_driver import ChatDriver
        from agent_db.stress.fixture import ArgumentFixture
        from agent_db.stress.preflight import chat_preflight
        from agent_db.stress.profile import LoadProfile

        profile = LoadProfile.model_validate(
            {
                "profile_version": 3,
                "name": "preflight-contract",
                "target_layer": "L3",
                "fixture": "sqlite-testseed",
                "tenants": {
                    "count": 1,
                    "distribution": "round_robin",
                    "scope": "separate",
                },
                "sessions": {
                    "pool_size": 1,
                    "recycle_after_turns": 40,
                    "recycle_after_seconds": 1800,
                    "min_interval_ms": 1200,
                },
                "arrival": {
                    "model": "constant",
                    "rps": 1,
                    "duration_s": 60,
                    "warmup_s": 10,
                },
                "clients": {"source_ips": 1, "generators": 1},
                "llm": {
                    "substrate": "stub",
                    "provider_concurrency": 256,
                    "p50_ms": 800,
                    "p95_ms": 2500,
                },
                "workload": [
                    {
                        "weight": 1.0,
                        "name": "one_tool",
                        "tools": ["db_get"],
                        "history_turns": 0,
                    }
                ],
                "budget_preset": "off",
            }
        )
        fixture = ArgumentFixture.model_validate(
            {
                "fixture_version": 1,
                "name": "preflight-contract",
                "scenario": "sqlite-testseed",
                "arguments": {"db_get": {"entity": "group", "id": "g1"}},
            }
        )
        transport = ChatTransport(stand.base_url, timeout_s=10.0)
        result = chat_preflight(
            profile=profile,
            fixture=fixture,
            transport=transport,
            driver=ChatDriver(transport),
            tenants=["t-1"],
            top_rps=1.0,
            budgets={},
            probe_tenants=False,
        )
        assert result.ok, result.refusals
        health = [c for c in result.checks if c["check"] == "health"]
        assert health and health[0]["status"] == "ok"
