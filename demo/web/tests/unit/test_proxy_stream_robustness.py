"""Pentest 2026-09-14 robustness ticket: silent SSE stream death on the proxy.

Two problems observed during the live round:

1. The shared httpx client applies ``WEB_PROXY_TIMEOUT`` (30s default) as the
   READ timeout of the SSE stream too — an agent turn that thinks/tool-calls
   for longer than that kills the proxy leg mid-stream.
2. When the upstream stream dies mid-flight, ``stream_gen`` lets the
   exception escape: the browser gets a silently truncated stream with no
   ``error``/``done`` event (the widget keeps showing "thinking" forever).

Contract pinned here:
- streaming proxy requests carry ``read=None`` (connect/write stay bounded);
- any mid-stream failure is converted into a terminal ``error`` + ``done``
  SSE pair, carrying the correlation id like api-service errors do.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from demo.settings import settings
from demo.web.server import app


@pytest.fixture
def client():
    http_client = httpx.AsyncClient(timeout=settings.web_proxy_timeout)
    app.state.http_client = http_client
    test_client = TestClient(app)
    yield test_client
    test_client.close()


def _streaming_upstream(chunks, fail_at_end: bool = False):
    async def generator():
        for chunk in chunks:
            yield chunk
        if fail_at_end:
            raise httpx.ReadTimeout("upstream stalled mid-stream")

    return httpx.Response(200, content=generator())


class TestStreamingProxyRobustness:
    @respx.mock
    def test_mid_stream_failure_emits_terminal_error_event(self, client):
        upstream = respx.post("http://127.0.0.1:8081/api/chat").mock(
            return_value=_streaming_upstream(
                [b'data: {"type": "session"}\n\n'], fail_at_end=True
            )
        )
        response = client.post("/api/chat", json={"message": "hi"})

        assert upstream.called
        assert response.status_code == 200
        text = response.text
        assert '"type": "error"' in text, (
            "a mid-stream upstream failure must reach the browser as a "
            f"terminal error event, got: {text!r}"
        )
        assert '"type": "done"' in text, "error must be followed by a done event"

    @respx.mock
    def test_streaming_request_uses_unbounded_read_timeout(self, client):
        upstream = respx.post("http://127.0.0.1:8081/api/chat").mock(
            return_value=_streaming_upstream([b'data: {"type": "done"}\n\n'])
        )
        response = client.post("/api/chat", json={"message": "hi"})

        assert response.status_code == 200
        timeout = upstream.calls.last.request.extensions.get("timeout")
        assert timeout is not None, "streaming request must set an explicit timeout"
        assert timeout.get("read") is None, (
            "SSE reads must not be bounded by WEB_PROXY_TIMEOUT; the agent "
            "may stay silent longer than that between events"
        )

    @respx.mock
    def test_healthy_stream_passes_through_unchanged(self, client):
        respx.post("http://127.0.0.1:8081/api/chat").mock(
            return_value=_streaming_upstream(
                [
                    b'data: {"type": "session"}\n\n',
                    b'data: {"type": "done"}\n\n',
                ]
            )
        )
        response = client.post("/api/chat", json={"message": "hi"})

        assert response.status_code == 200
        assert response.text.count("data:") == 2
        assert '"type": "error"' not in response.text


class TestProxyErrorHygiene:
    """Public-edge errors: localised for the visitor, sanitised for strangers.

    The terminal SSE error text is rendered in the chat bubble, so it follows
    the request's Accept-Language like api-service does. An unexpected proxy
    exception must not reach the browser verbatim: its message can carry
    internal hosts, paths or upstream credentials (AGENTS.md: public errors
    are retried as sanitised).
    """

    @respx.mock
    def test_interrupted_stream_text_follows_accept_language(self, client):
        respx.post("http://127.0.0.1:8081/api/chat").mock(
            return_value=_streaming_upstream([b'data: {"type": "x"}\n\n'], fail_at_end=True)
        )
        response = client.post(
            "/api/chat", json={"message": "hi"}, headers={"Accept-Language": "en-US,en;q=0.9"}
        )

        assert "The connection to the assistant was interrupted" in response.text
        assert "Соединение" not in response.text

    @respx.mock
    def test_unexpected_proxy_error_body_is_sanitised(self, client):
        respx.post("http://127.0.0.1:8081/api/chat").mock(
            side_effect=RuntimeError("db.internal:5432 leaked credential")
        )
        response = client.post("/api/chat", json={"message": "hi"})

        assert response.status_code == 500
        assert response.text == "Proxy error"
        assert "db.internal" not in response.text
