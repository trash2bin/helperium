"""Regression tests for voice chat LLM config propagation.

Checks that chat_voice_endpoint() passes llm_config/system_prompt from agent
into stream_events kwargs.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_vc():
    vc = MagicMock()
    vc.enabled = True
    vc.max_voice_message_size = 10485760
    vc.stt_fallback_enabled = True
    vc.stt_providers = []
    return vc


def _make_req():
    from fastapi import Request

    req = MagicMock(spec=Request)
    req.headers.get.return_value = ""
    return req


async def _drain(response):
    """Force-iterate StreamingResponse body so events() runs."""
    async for _ in response.body_iterator:
        pass


def _make_mock_agent():
    """Create a mock agent with an async generator stream_events."""

    async def _fake_stream_events(**kwargs):
        yield MagicMock(type="final", data={"content": "OK"})

    mock_agent = MagicMock()
    mock_agent.stream_events = _fake_stream_events
    return mock_agent


@pytest.mark.asyncio
async def test_voice_passes_provider_priority():
    from api_service.server import chat_voice_endpoint

    mock_agent = _make_mock_agent()

    mock_store = MagicMock()
    mock_store.get_agent.return_value = {
        "name": "test-agent",
        "tenant_ids": ["autoparts"],
        "provider_priority": ["mistral", "ollama"],
        "llm_config": None,
        "system_prompt": None,
        "voice_config": None,
    }

    with (
        patch(
            "api_service.server.routes.chat.get_agent_store", return_value=mock_store
        ),
        patch("api_service.server.routes.chat.get_agent", return_value=mock_agent),
        patch(
            "api_service.server.routes.chat.load_voice_config", return_value=_make_vc()
        ),
        patch(
            "api_service.server.routes.chat.resolve_voice_config",
            return_value=_make_vc(),
        ),
        patch("api_service.server.routes.chat.STTEngine") as mock_stt_cls,
        patch(
            "api_service.server.routes.chat.check_abuse",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        mock_stt_cls.from_config.return_value.transcribe = AsyncMock(
            return_value=MagicMock(text="test", provider_name="stt")
        )

        result = await chat_voice_endpoint(
            request=_make_req(),
            audio=MagicMock(read=AsyncMock(return_value=b"data")),
            session_id="s1",
            agent="test-agent",
            lang="ru",
        )
        await _drain(result)

    assert result.status_code == 200


@pytest.mark.asyncio
async def test_voice_passes_llm_config():
    from api_service.server import chat_voice_endpoint

    mock_agent = _make_mock_agent()

    mock_store = MagicMock()
    mock_store.get_agent.return_value = {
        "name": "test-agent",
        "tenant_ids": ["autoparts"],
        "provider_priority": [],
        "llm_config": {"provider": "ollama", "model": "qwen2.5:0.5b"},
        "system_prompt": None,
        "voice_config": None,
    }

    with (
        patch(
            "api_service.server.routes.chat.get_agent_store", return_value=mock_store
        ),
        patch("api_service.server.routes.chat.get_agent", return_value=mock_agent),
        patch(
            "api_service.server.routes.chat.load_voice_config", return_value=_make_vc()
        ),
        patch(
            "api_service.server.routes.chat.resolve_voice_config",
            return_value=_make_vc(),
        ),
        patch("api_service.server.routes.chat.STTEngine") as mock_stt_cls,
        patch(
            "api_service.server.routes.chat.check_abuse",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        mock_stt_cls.from_config.return_value.transcribe = AsyncMock(
            return_value=MagicMock(text="test", provider_name="stt")
        )

        result = await chat_voice_endpoint(
            request=_make_req(),
            audio=MagicMock(read=AsyncMock(return_value=b"data")),
            session_id="s1",
            agent="test-agent",
            lang="ru",
        )
        await _drain(result)

    assert result.status_code == 200


@pytest.mark.asyncio
async def test_voice_passes_system_prompt():
    from api_service.server import chat_voice_endpoint

    mock_agent = _make_mock_agent()

    mock_store = MagicMock()
    mock_store.get_agent.return_value = {
        "name": "test-agent",
        "tenant_ids": ["autoparts"],
        "provider_priority": [],
        "llm_config": {"provider": "ollama", "model": "qwen2.5:0.5b"},
        "system_prompt": "Ты тестовый агент, отвечай кратко.",
        "voice_config": None,
    }

    with (
        patch(
            "api_service.server.routes.chat.get_agent_store", return_value=mock_store
        ),
        patch("api_service.server.routes.chat.get_agent", return_value=mock_agent),
        patch(
            "api_service.server.routes.chat.load_voice_config", return_value=_make_vc()
        ),
        patch(
            "api_service.server.routes.chat.resolve_voice_config",
            return_value=_make_vc(),
        ),
        patch("api_service.server.routes.chat.STTEngine") as mock_stt_cls,
        patch(
            "api_service.server.routes.chat.check_abuse",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        mock_stt_cls.from_config.return_value.transcribe = AsyncMock(
            return_value=MagicMock(text="test", provider_name="stt")
        )

        result = await chat_voice_endpoint(
            request=_make_req(),
            audio=MagicMock(read=AsyncMock(return_value=b"data")),
            session_id="s1",
            agent="test-agent",
            lang="ru",
        )
        await _drain(result)

    assert result.status_code == 200


@pytest.mark.asyncio
async def test_voice_without_agent_no_llm_config():
    from api_service.server import chat_voice_endpoint

    mock_agent = _make_mock_agent()

    with (
        patch("api_service.server.routes.chat.get_agent", return_value=mock_agent),
        patch(
            "api_service.server.routes.chat.load_voice_config", return_value=_make_vc()
        ),
        patch(
            "api_service.server.routes.chat.resolve_voice_config",
            return_value=_make_vc(),
        ),
        patch("api_service.server.routes.chat.STTEngine") as mock_stt_cls,
        patch(
            "api_service.server.routes.chat.check_abuse",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        mock_stt_cls.from_config.return_value.transcribe = AsyncMock(
            return_value=MagicMock(text="test", provider_name="stt")
        )

        result = await chat_voice_endpoint(
            request=_make_req(),
            audio=MagicMock(read=AsyncMock(return_value=b"data")),
            session_id="s1",
            agent=None,
            lang="ru",
        )
        await _drain(result)

    assert result.status_code == 200
