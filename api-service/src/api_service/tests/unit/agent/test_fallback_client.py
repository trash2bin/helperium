"""Tests for LLM provider fallback (create_fallback_client + Router).

create_fallback_client() reads from ProviderStore, not settings.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import os

from api_service.agent.llm_client import LLMClient, create_fallback_client


def _make_mock_store(router_config: list[dict]) -> MagicMock:
    """Create a mock ProviderStore that returns the given router config."""
    store = MagicMock()
    store.get_active_router_config.return_value = router_config
    store.get_fallback_enabled.return_value = bool(router_config)
    return store


def _make_provider(model: str, api_key: str, api_base: str = "") -> dict:
    return {
        "model_name": "test",
        "litellm_params": {
            "model": model,
            "api_key": api_key,
            "timeout": 600,
            "temperature": 0.5,
            **({"api_base": api_base} if api_base else {}),
        },
    }


# Patch at the module level where create_fallback_client does the import
PATCH_STORE = "api_service.provider_store.get_provider_store"


class TestCreateFallbackClient:
    """Tests for create_fallback_client factory."""

    def test_fallback_disabled_uses_regular_client(self):
        """No providers in store -> regular LLMClient via create_client."""
        store = _make_mock_store([])
        with (
            patch(PATCH_STORE, return_value=store),
            patch("api_service.agent.llm_client.settings") as ms,
            patch.dict(os.environ, {}, clear=True),
        ):
            ms.ollama_model = "qwen2.5:0.5b"
            ms.ollama_url = "http://127.0.0.1:11434"
            ms.agent_temperature = 0.5
            ms.request_timeout = 600.0
            ms.agent_max_tokens_thinking = 4096
            ms.think_mode = False
            ms.mistral_api_key = ""

            client = create_fallback_client()
            assert isinstance(client, LLMClient)
            assert "ollama_chat" in client.model or "qwen" in client.model
            assert client.router is None

    def test_fallback_enabled_creates_router(self):
        """Active providers in store -> LLMClient with Router."""
        store = _make_mock_store(
            [
                _make_provider("openai/gpt-4o-mini", "sk-test-123"),
            ]
        )
        with (
            patch(PATCH_STORE, return_value=store),
            patch("api_service.agent.llm_client.settings") as ms,
        ):
            ms.agent_temperature = 0.5
            ms.request_timeout = 600.0
            ms.agent_max_tokens_thinking = 4096
            ms.think_mode = False

            client = create_fallback_client()
            assert isinstance(client, LLMClient)
            assert client.router is not None

    def test_fallback_multiple_providers(self):
        """Multiple providers in store -> Router with several models."""
        store = _make_mock_store(
            [
                _make_provider("openai/gpt-4o-mini", "sk-test-1"),
                _make_provider("anthropic/claude-3-haiku", "sk-ant-test"),
            ]
        )
        with (
            patch(PATCH_STORE, return_value=store),
            patch("api_service.agent.llm_client.settings") as ms,
        ):
            ms.agent_temperature = 0.5
            ms.request_timeout = 600.0
            ms.agent_max_tokens_thinking = 4096
            ms.think_mode = False

            client = create_fallback_client()
            assert isinstance(client, LLMClient)
            assert client.router is not None
            assert client.model == "openai/gpt-4o-mini"

    def test_fallback_router_in_llmclient(self):
        """LLMClient with router stores it for later use."""
        store = _make_mock_store(
            [
                _make_provider("openai/gpt-4o-mini", "sk-test"),
            ]
        )
        with (
            patch(PATCH_STORE, return_value=store),
            patch("api_service.agent.llm_client.settings") as ms,
        ):
            ms.agent_temperature = 0.5
            ms.request_timeout = 600.0
            ms.agent_max_tokens_thinking = 4096
            ms.think_mode = False

            client = create_fallback_client()
            assert client.router is not None
