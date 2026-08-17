"""Tenant authority regressions for public chat routes."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Request


class _SpyAgent:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def stream_events(self, *_args, **kwargs):
        self.calls.append(kwargs)
        yield SimpleNamespace(type="final", data={"content": "OK"})


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
    assert agent.calls == [
        {
            "session_id": "direct:scope-direct",
            "tenant_ids": ["configured-demo-tenant"],
            "lang": "en",
            "correlation_id": "tenant-scope-test",
        }
    ]


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
    assert agent.calls == [
        {
            "user_message": "hello",
            "session_id": "agent:composite-agent:scope-agent",
            "tenant_ids": ["tenant-a", "tenant-b"],
            "system_prompt": None,
            "lang": "en",
            "llm_config": None,
            "provider_priority": None,
            "correlation_id": "tenant-scope-test",
        }
    ]
