"""Regression tests from the 2026-09-13 authorized pentest of the local stack.

Three documented findings are pinned here as expected-fail tests:

1. Disguised bot User-Agents pass the blocklist because every pattern is
   anchored with ``^`` — ``"Mozilla/5.0 (compatible; curl/8.0)"`` is allowed
   while the contract says curl-like clients are blocked.
2. The 429 handler writes ``Retry-After: str(int(retry_after))`` — a fractional
   remaining delay (0 < retry_after < 1) is truncated to "0", telling the
   client to hammer immediately while the body announces a 1s wait.
3. A chat request without ``session_id`` silently joins the shared
   ``direct:default`` conversation instead of being rejected, so anonymous
   clients read and poison each other's transcript.

These tests are expected to FAIL against the vulnerable code. Each one
encodes the proposed post-fix contract; remove the failure by fixing the
implementation, not by deleting the test.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Request
from fastapi.responses import StreamingResponse

from api_service.anti_abuse import AbuseConfig, AntiAbuseChecker, TokenBucket


# ── 1. Disguised bot User-Agents ────────────────────────────────────────


@pytest.fixture
def checker() -> AntiAbuseChecker:
    return AntiAbuseChecker(AbuseConfig())


class TestDisguisedBotUserAgents:
    """Bot markers must be blocked wherever they appear in the UA string."""

    def test_curl_hidden_behind_browser_prefix(self, checker: AntiAbuseChecker):
        result = checker.check(
            "sess-1",
            "10.0.0.1",
            "Mozilla/5.0 (compatible; curl/8.0)",
            "hello",
            user_turn_count=1,
        )
        assert not result.allowed, (
            f"disguised curl UA passed the blocklist: {result.reason!r}"
        )

    def test_python_requests_hidden_behind_browser_prefix(
        self, checker: AntiAbuseChecker
    ):
        result = checker.check(
            "sess-1",
            "10.0.0.1",
            "Mozilla/5.0 (compatible; python-requests/2.31)",
            "hello",
            user_turn_count=1,
        )
        assert not result.allowed, (
            f"disguised python-requests UA passed the blocklist: {result.reason!r}"
        )

    def test_plain_browser_still_allowed(self, checker: AntiAbuseChecker):
        """Negative control: real browsers must keep working."""
        result = checker.check(
            "sess-1",
            "10.0.0.1",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X) AppleWebKit/537.36",
            "hello",
            user_turn_count=1,
        )
        assert result.allowed


# ── 2. Retry-After truncation to 0 ──────────────────────────────────────


class _RejectingIpBucket:
    def allow_ip(self, _ip: str):
        return (False, {"retry_after": 0.6})


class _StubProvider:
    def get_enforcers(self, _agent_cfg=None):
        return AntiAbuseChecker(AbuseConfig()), TokenBucket(AbuseConfig())

    def get_ip_bucket(self):
        return _RejectingIpBucket()


async def _receive() -> dict:
    return {"type": "http.request", "body": b"{}", "more_body": False}


def _request() -> Request:
    payload = json.dumps({"message": "hi", "session_id": "s"}).encode()

    async def receive() -> dict:
        return {"type": "http.request", "body": payload, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/chat",
            "headers": [
                (b"content-type", b"application/json"),
                (b"user-agent", b"Browser/1.0"),
            ],
            "client": ("203.0.113.7", 4321),
        },
        receive,
    )


@pytest.mark.asyncio
async def test_retry_after_header_never_announces_zero(monkeypatch) -> None:
    """A fractional retry delay must round UP to at least 1s, never to 0."""

    from api_service.server import security

    monkeypatch.setattr(
        security, "get_live_abuse_provider", lambda *_a, **_k: _StubProvider()
    )

    response = await security.check_abuse(_request(), "sess-ra", "hello")

    assert isinstance(response, StreamingResponse)
    assert response.status_code == 429
    header = response.headers.get("Retry-After")
    assert header is not None, "429 must carry a Retry-After header"
    assert int(header) >= 1, (
        f"Retry-After: {header!r} tells the client to retry immediately "
        "while the remaining delay is 0.6s — retry storm fuel"
    )


# ── 3. Missing session_id joins the shared global conversation ──────────


class _SpyAgent:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def stream_events(self, *_args, **kwargs):
        self.calls.append(kwargs)
        yield SimpleNamespace(type="final", data={"content": "OK"})


async def _drain(response) -> str:
    chunks: list[bytes] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode())
    return b"".join(chunks).decode()


@pytest.mark.asyncio
async def test_chat_without_session_id_is_rejected_not_shared() -> None:
    """Requests without session_id must fail closed, not join ``direct:default``.

    Today ``ChatRequest.session_id`` defaults to ``"default"``, so every
    session-less anonymous client shares one global conversation: they read
    each other's context and any of them can poison it.
    """
    from api_service.server.routes.chat import chat_endpoint

    agent = _SpyAgent()

    payload = json.dumps({"message": "hi"}).encode()

    async def receive() -> dict:
        return {"type": "http.request", "body": payload, "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/chat",
            "headers": [(b"content-type", b"application/json")],
        },
        receive,
    )
    request.state.correlation_id = "missing-session-test"

    with (
        patch("api_service.server.routes.chat.get_agent", return_value=agent),
        patch(
            "api_service.server.routes.chat.check_abuse",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        response = await chat_endpoint(request)
        body = await _drain(response)

    assert "Missing session_id" in body, (
        "a session-less request silently joined a shared conversation "
        f"instead of being rejected; stream body: {body[:200]!r}"
    )
    assert len(agent.calls) == 0, (
        "the agent was invoked for a session-less request — it would have "
        "run against (and appended to) the shared direct:default transcript"
    )
