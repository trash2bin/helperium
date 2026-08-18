from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from starlette.requests import Request

from demo.web.server import _get_proxy_headers, _proxy_to_api


async def _receive() -> dict:
    return {"type": "http.request", "body": b"{}", "more_body": False}


@pytest.mark.asyncio
async def test_stream_proxy_forwards_upstream_rate_limit() -> None:
    """A streamed upstream 429 must not become a proxy-side 500."""

    async def upstream(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat/demo-agent"
        return httpx.Response(
            429,
            headers={
                "content-type": "text/event-stream",
                "retry-after": "60",
            },
            content=b'{"detail":"Rate limit exceeded"}',
        )

    app = FastAPI()
    transport = httpx.MockTransport(upstream)
    async with httpx.AsyncClient(transport=transport) as client:
        app.state.http_client = client
        request = Request(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/api/chat/demo-agent",
                "raw_path": b"/api/chat/demo-agent",
                "query_string": b"",
                "headers": [(b"content-type", b"application/json")],
                "client": ("127.0.0.1", 12345),
                "server": ("testserver", 80),
                "app": app,
            },
            receive=_receive,
        )

        response = await _proxy_to_api(
            request, "/api/chat/demo-agent", stream=True
        )

    assert response.status_code == 429
    assert response.headers["retry-after"] == "60"
    assert response.body == b'{"detail":"Rate limit exceeded"}'


@pytest.mark.asyncio
async def test_proxy_preserves_forwarded_client_ip() -> None:
    """The API limiter must receive the visitor IP, not only the proxy peer."""

    request = Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/chat/demo-agent",
            "raw_path": b"/api/chat/demo-agent",
            "query_string": b"",
            "headers": [
                (b"x-forwarded-for", b"198.51.100.10, 172.18.0.7"),
            ],
            "client": ("172.18.0.7", 12345),
            "server": ("testserver", 80),
        },
        receive=_receive,
    )

    headers = await _get_proxy_headers(request)

    assert headers["x-forwarded-for"] == "198.51.100.10, 172.18.0.7"
