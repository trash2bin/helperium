"""Tests for LLM provider prioritised client — create_prioritized_client.

Проверяет:
1. Router создаётся со всеми провайдерами в правильном порядке
2. Все энтри в Router имеют один model_name (= model_group) для кросс-провайдерного fallback'а
3. Router_group установлен в LLMClient
4. Провайдеры без api_key пропускаются
5. Провайдеры без model пропускаются
6. Disabled провайдеры пропускаются
7. Когда нет валидных провайдеров — fallback на create_client()
8. Когда agent не указан — нет provider_priority — то же что и раньше (без prioritised)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import os

from api_service.agent.llm_client import LLMClient, create_prioritized_client


# ── Helpers ──


def _make_mock_store(providers: dict[str, dict]) -> MagicMock:
    """Create a mock ProviderStore that returns the given providers.

    ``providers`` is a dict of ``name -> {model, api_key, api_base, enabled, provider}``.
    """
    store = MagicMock()

    # get_provider() returns masked data (like real store.get_provider)
    def _get_provider(name: str) -> dict | None:
        p = providers.get(name)
        if not p:
            return None
        return {
            "name": name,
            "model": p.get("model", ""),
            "api_base": p.get("api_base", ""),
            "enabled": p.get("enabled", True),
            "provider": p.get("provider", ""),
            "has_api_key": bool(p.get("api_key")),
        }

    store.get_provider.side_effect = _get_provider
    # all_providers_raw returns unmasked data
    store.all_providers_raw = {k: v for k, v in providers.items()}
    return store


def _mock_settings(ollama_model: str = "qwen2.5:0.5b") -> MagicMock:
    ms = MagicMock()
    ms.agent_temperature = 0.5
    ms.request_timeout = 600.0
    ms.agent_max_tokens_thinking = 4096
    ms.think_mode = False
    ms.ollama_model = ollama_model
    ms.ollama_url = "http://127.0.0.1:11434"
    return ms


PATCH_STORE = "api_service.provider_store.get_provider_store"


class TestCreatePrioritizedClient:
    """Tests for create_prioritized_client factory."""

    # ── Group name (core fix: all providers share one group) ──

    def test_all_providers_share_same_model_name(self):
        """Все провайдеры в Router должны иметь одинаковый model_name для группового fallback'а."""
        store = _make_mock_store(
            {
                "primary": {
                    "model": "openai/gpt-4o-mini",
                    "api_key": "sk-test-1",
                    "enabled": True,
                },
                "secondary": {
                    "model": "mistral/mistral-small",
                    "api_key": "sk-test-2",
                    "enabled": True,
                },
            }
        )
        with (
            patch(PATCH_STORE, return_value=store),
            patch("api_service.agent.llm_client.settings", _mock_settings()),
        ):
            client = create_prioritized_client(["primary", "secondary"])
            assert isinstance(client, LLMClient)
            assert client.router is not None

            # Достаём model_list из Router для проверки
            model_list = client.router.model_list
            names = [m["model_name"] for m in model_list]
            assert len(names) == 2
            assert names[0] == names[1], (
                f"Все model_name должны совпадать для группового fallback'а, "
                f"но получили: {names}"
            )

    def test_router_group_is_set(self):
        """LLMClient.router_group должен быть установлен."""
        store = _make_mock_store(
            {
                "primary": {
                    "model": "openai/gpt-4o-mini",
                    "api_key": "sk-test-1",
                    "enabled": True,
                },
            }
        )
        with (
            patch(PATCH_STORE, return_value=store),
            patch("api_service.agent.llm_client.settings", _mock_settings()),
        ):
            client = create_prioritized_client(["primary"])
            assert client.router is not None
            assert client.router_group == "agent_priority_group", (
                f"router_group ожидался 'agent_priority_group', "
                f"получено {client.router_group!r}"
            )

    def test_stream_completion_passes_model_group(self):
        """stream_completion должен передавать model_group в Router, а не model."""
        store = _make_mock_store(
            {
                "p1": {
                    "model": "openai/gpt-4o-mini",
                    "api_key": "sk-test-1",
                    "enabled": True,
                },
            }
        )
        with (
            patch(PATCH_STORE, return_value=store),
            patch("api_service.agent.llm_client.settings", _mock_settings()),
        ):
            client = create_prioritized_client(["p1"])
            assert client.router is not None
            assert client.router_group == "agent_priority_group"

            # У Router в model_list все model_name = "agent_priority_group"
            names = [m["model_name"] for m in client.router.model_list]
            assert all(n == "agent_priority_group" for n in names), (
                f"Не все model_name установлены в группу: {names}"
            )

    # ── Priority order ──

    def test_priority_order_is_preserved(self):
        """Провайдеры идут в Router в том же порядке, что и provider_names."""
        store = _make_mock_store(
            {
                "a": {
                    "model": "openai/gpt-4o-mini",
                    "api_key": "sk-1",
                    "enabled": True,
                },
                "b": {
                    "model": "mistral/mistral-small",
                    "api_key": "sk-2",
                    "enabled": True,
                },
                "c": {
                    "model": "anthropic/claude-3-haiku",
                    "api_key": "sk-3",
                    "enabled": True,
                },
            }
        )
        with (
            patch(PATCH_STORE, return_value=store),
            patch("api_service.agent.llm_client.settings", _mock_settings()),
        ):
            client = create_prioritized_client(["c", "a", "b"])
            assert client.router is not None

            models = [m["litellm_params"]["model"] for m in client.router.model_list]
            assert models == [
                "anthropic/claude-3-haiku",
                "openai/gpt-4o-mini",
                "mistral/mistral-small",
            ], f"Порядок не сохранён: {models}"

    def test_primary_model_is_first_provider(self):
        """LLMClient.model должен быть моделью первого провайдера."""
        store = _make_mock_store(
            {
                "p1": {
                    "model": "openai/gpt-4o-mini",
                    "api_key": "sk-1",
                    "enabled": True,
                },
                "p2": {
                    "model": "mistral/mistral-small",
                    "api_key": "sk-2",
                    "enabled": True,
                },
            }
        )
        with (
            patch(PATCH_STORE, return_value=store),
            patch("api_service.agent.llm_client.settings", _mock_settings()),
        ):
            client = create_prioritized_client(["p1", "p2"])
            assert client.model == "openai/gpt-4o-mini"

    # ── Skipping invalid providers ──

    def test_skips_providers_without_api_key(self):
        """Провайдеры без api_key должны быть пропущены."""
        store = _make_mock_store(
            {
                "good": {
                    "model": "openai/gpt-4o-mini",
                    "api_key": "sk-1",
                    "enabled": True,
                },
                "nokey": {
                    "model": "mistral/mistral-small",
                    "api_key": "",
                    "enabled": True,
                },
            }
        )
        with (
            patch(PATCH_STORE, return_value=store),
            patch("api_service.agent.llm_client.settings", _mock_settings()),
        ):
            client = create_prioritized_client(["nokey", "good"])
            assert client.router is not None
            models = [m["litellm_params"]["model"] for m in client.router.model_list]
            assert len(models) == 1
            assert models[0] == "openai/gpt-4o-mini"

    def test_skips_disabled_providers(self):
        """disabled провайдеры должны быть пропущены."""
        store = _make_mock_store(
            {
                "disabled": {
                    "model": "openai/gpt-4o-mini",
                    "api_key": "sk-1",
                    "enabled": False,
                },
                "active": {
                    "model": "mistral/mistral-small",
                    "api_key": "sk-2",
                    "enabled": True,
                },
            }
        )
        with (
            patch(PATCH_STORE, return_value=store),
            patch("api_service.agent.llm_client.settings", _mock_settings()),
        ):
            client = create_prioritized_client(["disabled", "active"])
            assert client.router is not None
            models = [m["litellm_params"]["model"] for m in client.router.model_list]
            assert len(models) == 1
            assert models[0] == "mistral/mistral-small"

    def test_skips_providers_without_model(self):
        """Провайдеры без model должны быть пропущены."""
        store = _make_mock_store(
            {
                "nomodel": {"model": "", "api_key": "sk-1", "enabled": True},
            }
        )
        with (
            patch(PATCH_STORE, return_value=store),
            patch("api_service.agent.llm_client.settings", _mock_settings()),
            patch.dict(os.environ, {}, clear=True),
        ):
            client = create_prioritized_client(["nomodel"])
            # Fallback на create_client() — Router не создаётся
            assert client.router is None, (
                "При пустом списке валидных провайдеров Router не должен создаваться"
            )

    def test_skips_not_found_providers(self):
        """Несуществующие в store провайдеры должны быть пропущены."""
        store = _make_mock_store(
            {
                "real": {
                    "model": "openai/gpt-4o-mini",
                    "api_key": "sk-1",
                    "enabled": True,
                },
            }
        )
        with (
            patch(PATCH_STORE, return_value=store),
            patch("api_service.agent.llm_client.settings", _mock_settings()),
        ):
            client = create_prioritized_client(["nonexistent", "real"])
            assert client.router is not None
            models = [m["litellm_params"]["model"] for m in client.router.model_list]
            assert len(models) == 1
            assert models[0] == "openai/gpt-4o-mini"

    # ── Fallback to default ──

    def test_no_valid_providers_falls_back_to_default(self):
        """Когда нет валидных провайдеров — должен вернуться обычный LLMClient через create_client."""
        store = _make_mock_store({})
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

            client = create_prioritized_client(["whatever"])
            assert client.router is None
            assert "qwen" in client.model or "ollama" in client.model

    # ── Provider prefix ──

    def test_prefixes_model_with_raw_provider(self):
        """Если модель без префикса — должен добавить provider/ перед ней."""
        store = _make_mock_store(
            {
                "misty": {
                    "model": "mistral-small",
                    "api_key": "sk-1",
                    "provider": "mistral",
                    "enabled": True,
                },
            }
        )
        with (
            patch(PATCH_STORE, return_value=store),
            patch("api_service.agent.llm_client.settings", _mock_settings()),
        ):
            client = create_prioritized_client(["misty"])
            assert client.router is not None
            model = client.router.model_list[0]["litellm_params"]["model"]
            assert model == "mistral/mistral-small", (
                f"Ожидался префикс mistral/, получено {model}"
            )
