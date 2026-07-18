"""Regression tests for voice chat LLM config propagation.

Проверяет что chat_voice_endpoint() в server.py передаёт
в stream_events llm_client/llm_config/system_prompt из агента.

Вызывает endpoint напрямую и форсирует итерацию StreamingResponse.body_iterator
чтобы events() выполнился и trigger'нул stream_events.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_vc():
    vc = MagicMock()
    vc.enabled = True
    vc.max_voice_message_size = 10485760
    vc.stt_fallback_enabled = True
    vc.tts_fallback_enabled = False
    vc.stt_providers = []
    vc.tts_providers = []
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


@pytest.mark.asyncio
async def test_voice_passes_provider_priority():
    from api_service.server import chat_voice_endpoint

    mock_stream = AsyncMock()
    mock_stream.return_value.__aiter__.return_value = [
        MagicMock(type="final", data={"content": "OK"}),
    ]
    mock_agent = MagicMock()
    mock_agent.stream_events = mock_stream

    mock_prioritized = MagicMock()
    mock_prioritized.return_value = MagicMock(
        model="mistral/mistral-small",
        router=None,
        router_group=None,
        enable_thinking=False,
        last_usage=None,
        last_cost=0.0,
    )

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
        patch("api_service.server.get_agent_store", return_value=mock_store),
        patch("api_service.server.get_agent", return_value=mock_agent),
        patch(
            "api_service.agent.llm_client.create_prioritized_client", mock_prioritized
        ),
        patch("api_service.server.load_voice_config", return_value=_make_vc()),
        patch("api_service.server.resolve_voice_config", return_value=_make_vc()),
        patch("api_service.server.STTEngine.from_config") as mock_stt_factory,
        patch(
            "api_service.server._check_abuse", new_callable=AsyncMock, return_value=None
        ),
    ):
        mock_stt_factory.return_value.transcribe = AsyncMock(
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
    mock_prioritized.assert_called_once_with(["mistral", "ollama"])
    call_kwargs = mock_stream.call_args.kwargs or {}
    assert "llm_client" in call_kwargs, (
        f"Нет llm_client, есть: {list(call_kwargs.keys())}"
    )


@pytest.mark.asyncio
async def test_voice_passes_llm_config():
    from api_service.server import chat_voice_endpoint

    mock_stream = AsyncMock()
    mock_stream.return_value.__aiter__.return_value = [
        MagicMock(type="final", data={"content": "OK"}),
    ]
    mock_agent = MagicMock()
    mock_agent.stream_events = mock_stream

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
        patch("api_service.server.get_agent_store", return_value=mock_store),
        patch("api_service.server.get_agent", return_value=mock_agent),
        patch("api_service.server.load_voice_config", return_value=_make_vc()),
        patch("api_service.server.resolve_voice_config", return_value=_make_vc()),
        patch("api_service.server.STTEngine.from_config") as mock_stt_factory,
        patch(
            "api_service.server._check_abuse", new_callable=AsyncMock, return_value=None
        ),
    ):
        mock_stt_factory.return_value.transcribe = AsyncMock(
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
    call_kwargs = mock_stream.call_args.kwargs or {}
    assert "llm_client" in call_kwargs, (
        f"Нет llm_client, есть: {list(call_kwargs.keys())}"
    )


@pytest.mark.asyncio
async def test_voice_passes_system_prompt():
    from api_service.server import chat_voice_endpoint

    mock_stream = AsyncMock()
    mock_stream.return_value.__aiter__.return_value = [
        MagicMock(type="final", data={"content": "OK"}),
    ]
    mock_agent = MagicMock()
    mock_agent.stream_events = mock_stream

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
        patch("api_service.server.get_agent_store", return_value=mock_store),
        patch("api_service.server.get_agent", return_value=mock_agent),
        patch("api_service.server.load_voice_config", return_value=_make_vc()),
        patch("api_service.server.resolve_voice_config", return_value=_make_vc()),
        patch("api_service.server.STTEngine.from_config") as mock_stt_factory,
        patch(
            "api_service.server._check_abuse", new_callable=AsyncMock, return_value=None
        ),
    ):
        mock_stt_factory.return_value.transcribe = AsyncMock(
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
    call_kwargs = mock_stream.call_args.kwargs or {}
    assert call_kwargs.get("system_prompt") == "Ты тестовый агент, отвечай кратко.", (
        f"system_prompt не передан: {call_kwargs}"
    )


@pytest.mark.asyncio
async def test_voice_without_agent_no_llm_config():
    from api_service.server import chat_voice_endpoint

    mock_stream = AsyncMock()
    mock_stream.return_value.__aiter__.return_value = [
        MagicMock(type="final", data={"content": "OK"}),
    ]
    mock_agent = MagicMock()
    mock_agent.stream_events = mock_stream

    with (
        patch("api_service.server.get_agent", return_value=mock_agent),
        patch("api_service.server.load_voice_config", return_value=_make_vc()),
        patch("api_service.server.resolve_voice_config", return_value=_make_vc()),
        patch("api_service.server.STTEngine.from_config") as mock_stt_factory,
        patch(
            "api_service.server._check_abuse", new_callable=AsyncMock, return_value=None
        ),
    ):
        mock_stt_factory.return_value.transcribe = AsyncMock(
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
    call_kwargs = mock_stream.call_args.kwargs or {}
    assert "llm_client" not in call_kwargs or call_kwargs.get("llm_client") is None
    assert "llm_config" not in call_kwargs or call_kwargs.get("llm_config") is None
