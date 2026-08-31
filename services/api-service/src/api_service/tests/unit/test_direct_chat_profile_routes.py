"""Route-level regressions: DIRECT_CHAT_AGENT pins quality, never scope."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request

from api_service.server.tenant_authority import DirectChatProfile


class _SpyAgent:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def stream_events(self, *_args, **kwargs):
        self.calls.append(kwargs)
        yield SimpleNamespace(type="final", data={"content": "OK"})


def _request(body: dict, tenant_header: str = "") -> Request:
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
    request.state.correlation_id = "profile-test"
    return request


async def _drain(response) -> bytes:
    chunks: list[bytes] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode())
    return b"".join(chunks)


_PROFILE = DirectChatProfile(
    llm_config={"provider": "ollama", "model": "qwen2.5:0.5b"},
    system_prompt="Ты помощник каталога автозапчастей.",
    provider_priority=["polza", "ollama"],
)


@pytest.mark.asyncio
async def test_direct_chat_uses_pinned_profile_but_server_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pinned profile supplies prompt/provider only; tenant scope stays
    the server-configured default and the browser header stays irrelevant."""
    from api_service.server.routes.chat import chat_endpoint
    from api_service.server.routes import chat as chat_module

    monkeypatch.setattr(
        "api_service.server.tenant_authority.settings.default_tenant_id",
        "configured-demo-tenant",
    )
    agent = _SpyAgent()
    request = _request(
        {"message": "hello", "session_id": "profile-direct"},
        tenant_header="attacker-tenant",
    )

    with (
        patch("api_service.server.routes.chat.get_agent", return_value=agent),
        patch(
            "api_service.server.routes.chat.check_abuse",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch.object(chat_module, "direct_chat_profile", return_value=_PROFILE),
    ):
        response = await chat_endpoint(request)
        await _drain(response)

    assert response.status_code == 200
    assert len(agent.calls) == 1
    call = agent.calls[0]
    assert call["tenant_ids"] == ["configured-demo-tenant"]
    assert call["session_id"] == "direct:profile-direct"
    assert call["llm_config"] == _PROFILE.llm_config
    assert call["provider_priority"] == _PROFILE.provider_priority
    assert call["system_prompt"] == _PROFILE.system_prompt


@pytest.mark.asyncio
async def test_direct_chat_without_profile_keeps_pool_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No DIRECT_CHAT_AGENT: llm kwargs stay None, exactly as before."""
    from api_service.server.routes.chat import chat_endpoint

    monkeypatch.setattr(
        "api_service.server.tenant_authority.settings.default_tenant_id",
        "configured-demo-tenant",
    )
    agent = _SpyAgent()
    request = _request({"message": "hello", "session_id": "plain-direct"})

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

    call = agent.calls[0]
    assert call["tenant_ids"] == ["configured-demo-tenant"]
    assert call["llm_config"] is None
    assert call["provider_priority"] is None
    assert call["system_prompt"] is None


@pytest.mark.asyncio
async def test_voice_without_agent_uses_pinned_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Agent-less voice is direct chat: it inherits the pinned profile too."""
    from api_service.server.routes.chat import chat_voice_endpoint
    from api_service.server.routes import chat as chat_module

    monkeypatch.setattr(
        "api_service.server.tenant_authority.settings.default_tenant_id",
        "configured-demo-tenant",
    )
    captured: list[dict] = []

    async def _fake_stream_events(**kwargs):
        captured.append(kwargs)
        yield SimpleNamespace(type="final", data={"content": "OK"})

    mock_agent = MagicMock()
    mock_agent.stream_events = _fake_stream_events

    vc = MagicMock()
    vc.enabled = True
    vc.max_voice_message_size = 10485760

    request = MagicMock(spec=Request)
    request.headers.get.return_value = ""

    with (
        patch("api_service.server.routes.chat.get_agent", return_value=mock_agent),
        patch("api_service.server.routes.chat.load_voice_config", return_value=vc),
        patch("api_service.server.routes.chat.resolve_voice_config", return_value=vc),
        patch("api_service.server.routes.chat.STTEngine") as mock_stt_cls,
        patch(
            "api_service.server.routes.chat.check_abuse",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch.object(chat_module, "direct_chat_profile", return_value=_PROFILE),
    ):
        mock_stt_cls.from_config.return_value.transcribe = AsyncMock(
            return_value=MagicMock(text="тест", provider_name="stt")
        )
        response = await chat_voice_endpoint(
            request=request,
            audio=MagicMock(read=AsyncMock(return_value=b"data")),
            session_id="s1",
            agent=None,
            lang="ru",
        )
        async for _ in response.body_iterator:
            pass

    assert response.status_code == 200
    assert captured, "voice route must call stream_events"
    call = captured[0]
    assert call["tenant_ids"] == ["configured-demo-tenant"]
    assert call["llm_config"] == _PROFILE.llm_config
    assert call["provider_priority"] == _PROFILE.provider_priority
    assert call["system_prompt"] == _PROFILE.system_prompt
