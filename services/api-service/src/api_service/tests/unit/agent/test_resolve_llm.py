"""Integration tests for resolve_llm() provider resolution chain.

Covers the full priority cascade:
  scripted > llm_client > llm_config > provider_priority > pool/env fallback

Regression guards:
  - llm_config MUST win over provider_priority when both are provided.
  - provider_priority MUST be used when llm_config is absent.
  - Scripted provider MUST override everything.
  - Pool fallback MUST kick in when provider_priority has no match.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from api_service.agent.litellm_provider import LiteLLMProvider
from api_service.agent.provider_pool import FallbackProvider


# ── Helpers ──────────────────────────────────────────────────────────────


def _patch_scripted(return_value=None):
    """Patch create_scripted_provider to return None (no scripted mode)."""
    return patch(
        "api_service.agent.scripted_provider.create_scripted_provider",
        return_value=return_value,
    )


def _patch_scripted_active(provider):
    """Patch create_scripted_provider to return an active scripted provider."""
    return patch(
        "api_service.agent.scripted_provider.create_scripted_provider",
        return_value=provider,
    )


def _patch_pool(worker=None):
    """Patch _pool.get_any_worker to return a worker or None."""
    mock_pool = MagicMock()
    mock_pool.get_any_worker = AsyncMock(return_value=worker)
    return patch("api_service.agent.factory._pool", mock_pool)


def _patch_store(providers: dict, *, fallback_enabled: bool = True):
    """Patch ProviderStore to return specific providers.

    The import inside resolve_llm() is lazy:
        from api_service.provider_store import get_provider_store
    so we must patch the source module.
    """
    mock_store = MagicMock()
    mock_store.all_providers_raw = providers
    mock_store.get_fallback_enabled.return_value = fallback_enabled
    return patch(
        "api_service.provider_store.get_provider_store",
        return_value=mock_store,
    )


# ── Test: Scripted provider overrides everything ─────────────────────────


class TestScriptedOverride:
    """Scripted provider (USE_SCRIPTED_LLM=1) must win over all other sources."""

    @pytest.mark.asyncio
    async def test_scripted_wins_over_llm_config(self):
        scripted = MagicMock(spec=LiteLLMProvider)
        scripted.model = "scripted-test"

        with _patch_scripted_active(scripted):
            from api_service.agent.factory import resolve_llm

            result = await resolve_llm(
                llm_config={
                    "model": "openai/gpt-4",
                    "provider": "openai",
                    "api_key": "sk-test",
                },
            )

        assert result is scripted
        assert result.model == "scripted-test"

    @pytest.mark.asyncio
    async def test_scripted_wins_over_provider_priority(self):
        scripted = MagicMock(spec=LiteLLMProvider)
        scripted.model = "scripted-test"

        with _patch_scripted_active(scripted):
            from api_service.agent.factory import resolve_llm

            result = await resolve_llm(
                provider_priority=["ollama"],
            )

        assert result is scripted

    @pytest.mark.asyncio
    async def test_scripted_wins_over_llm_client(self):
        scripted = MagicMock(spec=LiteLLMProvider)
        scripted.model = "scripted-test"

        real_client = MagicMock(spec=LiteLLMProvider)
        real_client.model = "real-client"

        with _patch_scripted_active(scripted):
            from api_service.agent.factory import resolve_llm

            result = await resolve_llm(llm_client=real_client)

        assert result is scripted


# ── Test: llm_client (explicit) wins over config & priority ──────────────


class TestExplicitLlmClient:
    """Explicit llm_client parameter wins over everything except scripted."""

    @pytest.mark.asyncio
    async def test_llm_client_wins_over_llm_config(self):
        explicit = MagicMock(spec=LiteLLMProvider)
        explicit.model = "explicit-client"

        with _patch_scripted(return_value=None):
            from api_service.agent.factory import resolve_llm

            result = await resolve_llm(
                llm_client=explicit,
                llm_config={
                    "model": "openai/gpt-4",
                    "provider": "openai",
                    "api_key": "sk-test",
                },
            )

        assert result is explicit
        assert result.model == "explicit-client"

    @pytest.mark.asyncio
    async def test_llm_client_wins_over_provider_priority(self):
        explicit = MagicMock(spec=LiteLLMProvider)
        explicit.model = "explicit-client"

        with _patch_scripted(return_value=None):
            from api_service.agent.factory import resolve_llm

            result = await resolve_llm(
                llm_client=explicit,
                provider_priority=["ollama"],
            )

        assert result is explicit


# ── Test: llm_config wins over provider_priority ────────────────────────
# REGRESSION: the original bug was that chat.py passed provider_priority
# instead of llm_config when both were present.


class TestLlmConfigIsFirstPriorityCandidate:
    """llm_config MUST remain the first candidate when priority is also provided.

    This is the regression test for the bug where chat.py did:
        if provider_priority: kwargs["provider_priority"] = pp
        elif llm_config: kwargs["llm_config"] = cfg
    which meant provider_priority always won.
    """

    @pytest.mark.asyncio
    async def test_llm_config_is_primary_with_fallback_candidates(self):
        """The configured model starts first, then named fallbacks are retained."""
        providers = {
            "ollama": {
                "model": "minimax-m3:cloud",
                "api_key": "",
                "api_base": "http://localhost:11434",
                "enabled": True,
                "provider": "ollama",
            }
        }
        with (
            _patch_scripted(return_value=None),
            _patch_store(providers),
            _patch_pool(None),
        ):
            from api_service.agent.factory import resolve_llm

            result = await resolve_llm(
                llm_config={
                    "model": "openai/deepseek-v4-flash",
                    "provider": "openai",
                    "api_base": "https://polza.ai/api/v1",
                    "api_key": "pza_test-key",
                },
                provider_priority=["ollama"],
            )

        assert isinstance(result, FallbackProvider)
        assert result.model == "openai/deepseek-v4-flash"
        assert [provider.model for provider in result._providers] == [
            "openai/deepseek-v4-flash",
            "minimax-m3:cloud",
        ]

    @pytest.mark.asyncio
    async def test_llm_config_is_first_of_multiple_priority_candidates(self):
        """The configured model remains first even with several fallbacks."""
        providers = {
            "ollama": {
                "model": "minimax-m3:cloud",
                "enabled": True,
                "provider": "ollama",
            },
            "openai": {
                "model": "gpt-4o-mini",
                "enabled": True,
                "provider": "openai",
            },
            "mistral": {
                "model": "mistral-small",
                "enabled": True,
                "provider": "mistral",
            },
        }
        with (
            _patch_scripted(return_value=None),
            _patch_store(providers),
            _patch_pool(None),
        ):
            from api_service.agent.factory import resolve_llm

            result = await resolve_llm(
                llm_config={
                    "model": "anthropic/claude-3",
                    "provider": "anthropic",
                    "api_key": "sk-ant-test",
                },
                provider_priority=["ollama", "openai", "mistral"],
            )

        assert isinstance(result, FallbackProvider)
        assert result.model == "anthropic/claude-3"
        assert [provider.model for provider in result._providers] == [
            "anthropic/claude-3",
            "minimax-m3:cloud",
            "gpt-4o-mini",
            "mistral-small",
        ]

    @pytest.mark.asyncio
    async def test_llm_config_empty_dict_falls_through(self):
        """Empty llm_config (falsy) falls through to priority/pool."""
        with _patch_scripted(return_value=None), _patch_pool(None):
            from api_service.agent.factory import resolve_llm

            result = await resolve_llm(
                llm_config={},
                provider_priority=None,
            )

        # Should reach pool/env fallback, not crash
        assert isinstance(result, LiteLLMProvider)


# ── Test: provider_priority resolution ──────────────────────────────────


class TestProviderPriority:
    """When llm_config is absent, provider_priority is used."""

    @pytest.mark.asyncio
    async def test_provider_priority_picks_first_enabled(self):
        providers = {
            "ollama": {
                "model": "ollama_chat/minimax-m3:cloud",
                "api_key": "",
                "api_base": "http://localhost:11434",
                "enabled": True,
                "provider": "ollama",
            },
            "openai": {
                "model": "openai/gpt-4o-mini",
                "api_key": "sk-test",
                "api_base": "",
                "enabled": True,
                "provider": "openai",
            },
        }

        with (
            _patch_scripted(return_value=None),
            _patch_store(providers),
            _patch_pool(None),
        ):
            from api_service.agent.factory import resolve_llm

            result = await resolve_llm(provider_priority=["ollama", "openai"])

        assert isinstance(result, FallbackProvider)
        assert [provider.model for provider in result._providers] == [
            "ollama_chat/minimax-m3:cloud",
            "openai/gpt-4o-mini",
        ]

    @pytest.mark.asyncio
    async def test_provider_priority_forwards_store_api_key(self):
        """Store API key must reach LiteLLMProvider for custom providers."""
        providers = {
            "nvidia-nim": {
                "model": "nvidia_nim/nvidia/nemotron-3.5-lightning-30b-a3b",
                "api_key": "nvapi-test-only",
                "api_base": "https://integrate.api.nvidia.com/v1/",
                "enabled": True,
                "provider": "nvidia_nim",
            },
        }
        with (
            _patch_scripted(return_value=None),
            _patch_store(providers),
            _patch_pool(None),
        ):
            from api_service.agent.factory import resolve_llm

            result = await resolve_llm(provider_priority=["nvidia-nim"])
        assert isinstance(result, LiteLLMProvider)
        assert result.api_key == "nvapi-test-only"
        assert result.api_base == "https://integrate.api.nvidia.com/v1/"

    @pytest.mark.asyncio
    async def test_global_fallback_switch_keeps_only_the_primary_candidate(self):
        providers = {
            "ollama": {
                "model": "minimax-m3:cloud",
                "enabled": True,
                "provider": "ollama",
            },
            "openai": {
                "model": "gpt-4o-mini",
                "enabled": True,
                "provider": "openai",
            },
        }
        with (
            _patch_scripted(return_value=None),
            _patch_store(providers, fallback_enabled=False),
            _patch_pool(None),
        ):
            from api_service.agent.factory import resolve_llm

            result = await resolve_llm(provider_priority=["ollama", "openai"])

        assert isinstance(result, LiteLLMProvider)
        assert result.model == "minimax-m3:cloud"

    @pytest.mark.asyncio
    async def test_provider_priority_skips_disabled(self):
        providers = {
            "ollama": {
                "model": "ollama_chat/minimax-m3:cloud",
                "api_key": "",
                "api_base": "http://localhost:11434",
                "enabled": False,
                "provider": "ollama",
            },
            "openai": {
                "model": "openai/gpt-4o-mini",
                "api_key": "sk-test",
                "api_base": "",
                "enabled": True,
                "provider": "openai",
            },
        }

        with (
            _patch_scripted(return_value=None),
            _patch_store(providers),
            _patch_pool(None),
        ):
            from api_service.agent.factory import resolve_llm

            result = await resolve_llm(provider_priority=["ollama", "openai"])

        assert isinstance(result, LiteLLMProvider)
        assert result.model == "openai/gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_provider_priority_skips_missing(self):
        """Non-existent provider in priority list is skipped."""
        providers = {
            "openai": {
                "model": "openai/gpt-4o-mini",
                "api_key": "sk-test",
                "api_base": "",
                "enabled": True,
                "provider": "openai",
            },
        }

        with (
            _patch_scripted(return_value=None),
            _patch_store(providers),
            _patch_pool(None),
        ):
            from api_service.agent.factory import resolve_llm

            result = await resolve_llm(
                provider_priority=["ollama", "nonexistent", "openai"]
            )

        assert isinstance(result, LiteLLMProvider)
        assert result.model == "openai/gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_provider_priority_empty_list_falls_through(self):
        """Empty provider_priority list falls through to pool/env."""
        with _patch_scripted(return_value=None), _patch_pool(None):
            from api_service.agent.factory import resolve_llm

            result = await resolve_llm(provider_priority=[])

        # Should reach pool/env fallback
        assert isinstance(result, LiteLLMProvider)

    @pytest.mark.asyncio
    async def test_provider_priority_all_missing_falls_through(self):
        """All providers in priority are missing → pool/env fallback."""
        with _patch_scripted(return_value=None), _patch_pool(None):
            from api_service.agent.factory import resolve_llm

            result = await resolve_llm(
                provider_priority=["nonexistent1", "nonexistent2"]
            )

        assert isinstance(result, LiteLLMProvider)


# ── Test: pool / env fallback ───────────────────────────────────────────


class TestPoolEnvFallback:
    """When nothing else matches, ProviderPool or env-based provider is used."""

    @pytest.mark.asyncio
    async def test_pool_worker_used_when_available(self):
        pool_worker = MagicMock(spec=LiteLLMProvider)
        pool_worker.model = "pool-worker-model"

        with _patch_scripted(return_value=None), _patch_pool(pool_worker):
            from api_service.agent.factory import resolve_llm

            result = await resolve_llm()

        assert result is pool_worker

    @pytest.mark.asyncio
    async def test_env_fallback_when_pool_empty(self):
        with _patch_scripted(return_value=None), _patch_pool(None):
            from api_service.agent.factory import resolve_llm

            result = await resolve_llm()

        assert isinstance(result, LiteLLMProvider)


# ── Test: llm_config parameter details ──────────────────────────────────


class TestLlmConfigDetails:
    """Test that llm_config parameters are correctly applied."""

    @pytest.mark.asyncio
    async def test_provider_config_preserves_api_base(self):
        """Factory selects config without embedding provider transport policy."""
        with _patch_scripted(return_value=None):
            from api_service.agent.factory import resolve_llm

            result = await resolve_llm(
                llm_config={
                    "model": "mistral/mistral-medium-latest",
                    "provider": "mistral",
                    "api_key": "sk-mistral-test",
                    "api_base": "https://custom.example.com",
                },
            )

        assert isinstance(result, LiteLLMProvider)
        assert result.api_base == "https://custom.example.com"
        assert result.provider == "mistral"

    @pytest.mark.asyncio
    async def test_factory_preserves_raw_model_and_provider(self):
        """LiteLLM adapter, not factory, owns model/provider transport routing."""
        with _patch_scripted(return_value=None):
            from api_service.agent.factory import resolve_llm

            result = await resolve_llm(
                llm_config={
                    "model": "minimax-m3:cloud",
                    "provider": "ollama",
                    "api_base": "http://localhost:11434",
                },
            )

        assert isinstance(result, LiteLLMProvider)
        assert result.model == "minimax-m3:cloud"
        assert result.provider == "ollama"
        assert result.api_base == "http://localhost:11434"

    @pytest.mark.asyncio
    async def test_custom_temperature_and_max_tokens(self):
        """llm_config temperature and max_tokens override defaults."""
        with _patch_scripted(return_value=None):
            from api_service.agent.factory import resolve_llm

            result = await resolve_llm(
                llm_config={
                    "model": "openai/gpt-4o",
                    "provider": "openai",
                    "api_key": "sk-test",
                    "temperature": 0.1,
                    "max_tokens": 1024,
                },
            )

        assert isinstance(result, LiteLLMProvider)
        assert result.temperature == 0.1
        assert result.max_tokens_thinking == 1024


# ── Test: end-to-end resolution chain ───────────────────────────────────


class TestEndToEndResolution:
    """Full chain tests simulating real-world scenarios."""

    @pytest.mark.asyncio
    async def test_autoparts_scenario_polza_wins(self):
        """Regression: autoparts agent with provider_priority=["ollama"]
        + llm_config={polza} → must use polza, not Ollama."""
        providers = {
            "ollama": {
                "model": "ollama_chat/minimax-m3:cloud",
                "api_key": "",
                "api_base": "http://localhost:11434",
                "enabled": True,
                "provider": "ollama",
            },
        }

        with (
            _patch_scripted(return_value=None),
            _patch_store(providers),
            _patch_pool(None),
        ):
            from api_service.agent.factory import resolve_llm

            result = await resolve_llm(
                llm_config={
                    "model": "openai/deepseek-v4-flash",
                    "provider": "openai",
                    "api_base": "https://polza.ai/api/v1",
                    "api_key": "pza_test-key",
                },
                provider_priority=["ollama"],
            )

        assert isinstance(result, FallbackProvider)
        assert [provider.model for provider in result._providers] == [
            "openai/deepseek-v4-flash",
            "ollama_chat/minimax-m3:cloud",
        ]

    @pytest.mark.asyncio
    async def test_no_config_no_priority_uses_pool(self):
        """No agent config at all → pool or env fallback."""
        with _patch_scripted(return_value=None), _patch_pool(None):
            from api_service.agent.factory import resolve_llm

            result = await resolve_llm()

        assert isinstance(result, LiteLLMProvider)

    @pytest.mark.asyncio
    async def test_only_provider_priority_no_llm_config(self):
        """Agent has only provider_priority, no llm_config → use priority."""
        providers = {
            "mistral": {
                "model": "mistral/mistral-medium-latest",
                "api_key": "sk-mistral-test",
                "api_base": "",
                "enabled": True,
                "provider": "mistral",
            },
        }

        with (
            _patch_scripted(return_value=None),
            _patch_store(providers),
            _patch_pool(None),
        ):
            from api_service.agent.factory import resolve_llm

            result = await resolve_llm(provider_priority=["mistral"])

        assert isinstance(result, LiteLLMProvider)
        assert "mistral" in result.model
