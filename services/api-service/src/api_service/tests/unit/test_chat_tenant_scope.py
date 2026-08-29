"""Tenant authority regressions for public chat routes."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request


class _SpyAgent:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def stream_events(self, *_args, **kwargs):
        self.calls.append(kwargs)
        yield SimpleNamespace(type="final", data={"content": "OK"})


class _CancelledAgent:
    async def stream_events(self, *_args, **_kwargs):
        if False:  # Keep this an async generator for the production contract.
            yield SimpleNamespace(type="final", data={})
        raise asyncio.CancelledError


def _request(body: dict, tenant_header: str = "") -> Request:
    """Build a real ASGI request so SlowAPI executes its production wrapper."""
    payload = json.dumps(body).encode()

    async def receive() -> dict:
        return {"type": "http.request", "body": payload, "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/chat",
            "headers": [(b"content-type", b"application/json")]
            + ([(b"x-tenant-id", tenant_header.encode())] if tenant_header else []),
        },
        receive,
    )
    request.state.correlation_id = "tenant-scope-test"
    return request


async def _drain(response) -> bytes:
    chunks: list[bytes] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode())
    return b"".join(chunks)


@pytest.mark.asyncio
async def test_direct_chat_ignores_browser_tenant_header(monkeypatch) -> None:
    """Public direct chat uses only the server-configured safe demo tenant."""
    from api_service.server.routes.chat import chat_endpoint

    monkeypatch.setattr(
        "api_service.server.tenant_authority.settings.default_tenant_id",
        "configured-demo-tenant",
    )
    agent = _SpyAgent()
    request = _request(
        {"message": "hello", "session_id": "scope-direct"},
        tenant_header="private-tenant,another-private-tenant",
    )

    with (
        patch("api_service.server.routes.chat.get_agent", return_value=agent),
        patch(
            "api_service.server.routes.chat.check_abuse",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        response = await chat_endpoint(request)
        await _drain(response)

    assert response.status_code == 200
    assert len(agent.calls) == 1
    call = agent.calls[0]
    assert call["session_id"] == "direct:scope-direct"
    assert call["tenant_ids"] == ["configured-demo-tenant"]
    assert call["lang"] == "en"
    assert call["correlation_id"] == "tenant-scope-test"
    # Route must propagate a client-disconnect probe down to the agent.
    assert callable(call.get("disconnect_check"))


@pytest.mark.asyncio
async def test_direct_chat_emits_error_and_done_after_internal_cancellation() -> None:
    """Backend cancellation cannot silently strand an already-connected SSE client."""

    from api_service.server.routes.chat import chat_endpoint

    request = _request({"message": "hello", "session_id": "cancelled-stream"})
    with (
        patch(
            "api_service.server.routes.chat.get_agent",
            return_value=_CancelledAgent(),
        ),
        patch(
            "api_service.server.routes.chat.check_abuse",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        response = await chat_endpoint(request)
        body = (await _drain(response)).decode()

    assert response.status_code == 200
    assert "The data service connection was interrupted. Please try again." in body
    assert '"type": "error"' in body
    assert '"type": "done"' in body


@pytest.mark.asyncio
async def test_named_agent_uses_persisted_composite_scope_not_request_header() -> None:
    """Only Agent Store may authorize a composite tenant scope for public chat."""
    from api_service.server.routes.chat import chat_agent_handler

    agent = _SpyAgent()
    persisted_agent = {
        "name": "composite-agent",
        "tenant_ids": ["tenant-a", "tenant-b"],
        "llm_config": None,
        "system_prompt": None,
        "provider_priority": [],
        "abuse_config": None,
    }
    request = _request(
        {"message": "hello", "session_id": "scope-agent"},
        tenant_header="attacker-tenant",
    )

    with (
        patch("api_service.server.routes.chat.get_agent", return_value=agent),
        # get_agent_store() is evaluated eagerly before the (mocked)
        # asyncio.to_thread call; patch it so the test never opens the
        # developer's real agents.sqlite.
        patch(
            "api_service.server.routes.chat.get_agent_store",
            return_value=MagicMock(),
        ),
        patch(
            "api_service.server.routes.chat.asyncio.to_thread",
            new=AsyncMock(return_value=persisted_agent),
        ),
        patch(
            "api_service.server.routes.chat.check_abuse",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        response = await chat_agent_handler.__wrapped__(request, "composite-agent")
        body = await _drain(response)

    assert response.status_code == 200
    assert agent.calls, body.decode()
    call = agent.calls[0]
    assert call["user_message"] == "hello"
    assert call["session_id"] == "agent:composite-agent:scope-agent"
    assert call["tenant_ids"] == ["tenant-a", "tenant-b"]
    assert call["system_prompt"] is None
    assert call["lang"] == "en"
    assert call["llm_config"] is None
    assert call["provider_priority"] is None
    assert call["correlation_id"] == "tenant-scope-test"
    # Route must propagate a client-disconnect probe down to the agent.
    assert callable(call.get("disconnect_check"))


@pytest.mark.asyncio
async def test_disconnect_watcher_is_started_and_stopped() -> None:
    """Regression: a watcher that is never started cannot latch a disconnect,
    so abandoned turns kept consuming provider budget and tool calls."""
    from api_service.server.routes.chat import chat_endpoint
    from api_service.server.routes import chat as chat_module

    started: list[object] = []
    original_start = chat_module._DisconnectWatch.start

    def spy_start(self: chat_module._DisconnectWatch) -> None:
        started.append(self)
        original_start(self)

    agent = _SpyAgent()
    request = _request({"message": "hello", "session_id": "watcher-lifecycle"})

    with (
        patch("api_service.server.routes.chat.get_agent", return_value=agent),
        patch(
            "api_service.server.routes.chat.check_abuse",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch.object(chat_module._DisconnectWatch, "start", spy_start),
    ):
        response = await chat_endpoint(request)
        await _drain(response)

    assert response.status_code == 200
    # The watcher must have been started exactly once and stopped afterwards.
    assert len(started) == 1
    watcher = started[0]
    assert watcher is not None
    assert watcher._task is None  # stop() ran and cleared the task


@pytest.mark.asyncio
async def test_disconnect_watcher_latch_stops_agent_stream() -> None:
    """Once the watcher latches a disconnect, the buffered SSE stream must
    terminate with a done event instead of hanging on the queue."""
    from api_service.server.routes.chat import (
        _buffered_agent_sse_events,
        _DisconnectWatch,
    )

    latch = _DisconnectWatch(_request({}))

    async def source():
        yield SimpleNamespace(type="final", data={"content": "first"})
        # Simulate the watcher latching between provider events.
        latch._event.set()
        yield SimpleNamespace(type="final", data={"content": "second"})

    received: list[str] = []
    async for chunk in _buffered_agent_sse_events(
        source(), "en", "cid", disconnect_check=latch.check
    ):
        received.append(chunk)

    assert any('"type": "done"' in chunk for chunk in received)
    # The second event must never be delivered after the latch.
    assert not any("second" in chunk for chunk in received)


@pytest.mark.asyncio
async def test_buffered_stream_terminates_on_disconnect_before_first_event() -> None:
    """Producer must emit a terminal event even when the disconnect is detected
    before the first source event, instead of leaving the consumer blocked."""
    from api_service.server.routes.chat import (
        _buffered_agent_sse_events,
        _DisconnectWatch,
    )

    latch = _DisconnectWatch(_request({}))
    latch._event.set()

    async def source():
        yield SimpleNamespace(type="final", data={"content": "never"})
        yield SimpleNamespace(type="final", data={"content": "also never"})

    received: list[str] = []
    async for chunk in _buffered_agent_sse_events(
        source(), "en", "cid", disconnect_check=latch.check
    ):
        received.append(chunk)

    assert received == [] or any('"type": "done"' in chunk for chunk in received)
    assert not any("never" in chunk for chunk in received)


@pytest.mark.asyncio
async def test_mcp_logs_carry_correlation_id_from_contextvar() -> None:
    """Correlation id set by the route must reach structured MCP log records
    (JSON payload), so tool-call failures can be tied to a chat turn."""
    import json as _json
    import logging

    from api_service.log_config import (
        build_log_formatter,
        configure_logging,
        correlation_id_var,
    )

    configure_logging()  # ensure structlog processors are installed
    correlation_id_var.set("corr-mcp-123")
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Capture()
    test_logger = logging.getLogger("api_service.agent.mcp_client")
    test_logger.addHandler(handler)
    try:
        test_logger.warning("[MCP] correlation test message")
    finally:
        test_logger.removeHandler(handler)

    assert records, "expected the warning record to reach the handler"
    # Stdlib-origin records are rendered by the ProcessorFormatter installed
    # on the root handler; format the captured record through a fresh
    # formatter instance to verify the foreign_pre_chain injects the
    # correlation id from the contextvar.
    rendered = build_log_formatter().format(records[0])
    payload = _json.loads(rendered)
    assert payload["correlation_id"] == "corr-mcp-123"
    assert payload["event"] == "[MCP] correlation test message"
