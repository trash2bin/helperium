"""Regression: provider_priority must not be the last word at execution time.

Live incident (2026-09-12, demo loop): the `autoparts-assistant` agent is
pinned in the store to provider_priority=["nvidia-nim-nemotron-35-lightning"].
Three times during the session the NIM endpoint silently timed out at 60s
and the widget answered «Модель временно недоступна» — while the
ProviderPool held healthy, enabled workers (`openai/gpt-4o-mini`, polza,
ollama) that were never even touched.

`resolve_llm` treats named candidates as authoritative: with one named
provider it returns a bare single provider, with several it builds a
FallbackProvider over exactly those. The pool is only consulted when the
priority list is empty or names nothing. So a pinned-but-dead provider
kills the request even though a healthy worker is one call away.

Regression contract: the ProviderPool worker must be kept as the FINAL
fallback candidate behind any named candidates, so a dead pinned provider
(or a fully dead priority list) still succeeds through the pool.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api_service.agent.answer_normalizer import AnswerNormalizer
from api_service.agent.litellm_provider import LiteLLMProvider
from api_service.agent.models import CompletionRequest, CompletionResponse
from api_service.agent.provider_pool import FallbackProvider


# ── Test data mirroring the live autoparts store ─────────────────────────


def _nim_provider_config() -> dict:
    return {
        "model": "nvidia_nim/nvidia/nemotron-3.5-lightning-30b-a3b",
        "api_key": "nvapi-live-regression",
        "api_base": "https://integrate.api.nvidia.com/v1/",
        "enabled": True,
        "provider": "nvidia_nim",
    }


def _healthy_pool_worker() -> MagicMock:
    worker = MagicMock(spec=LiteLLMProvider)
    worker.model = "openai/gpt-4o-mini"
    worker.provider = "openai"
    worker.api_base = ""
    worker.complete = AsyncMock(
        return_value=CompletionResponse(content="answer from pool worker")
    )
    return worker


def _request() -> CompletionRequest:
    return CompletionRequest(messages=[{"role": "user", "content": "ping"}])


def _patch_scripted(return_value=None):
    return patch(
        "api_service.agent.scripted_provider.create_scripted_provider",
        return_value=return_value,
    )


def _patch_pool(worker=None):
    mock_pool = MagicMock()
    mock_pool.get_any_worker = AsyncMock(return_value=worker)
    return patch("api_service.agent.factory._pool", mock_pool)


def _patch_store(providers: dict, *, fallback_enabled: bool = True):
    mock_store = MagicMock()
    mock_store.all_providers_raw = providers
    mock_store.get_fallback_enabled.return_value = fallback_enabled
    return patch(
        "api_service.provider_store.get_provider_store",
        return_value=mock_store,
    )


class TestPoolFallbackWhenPriorityProviderIsDead:
    """The live defect: a pinned, unresponsive provider must fall through to
    a healthy pool worker instead of killing the request."""

    @pytest.mark.asyncio
    async def test_single_dead_priority_provider_falls_back_to_pool(self):
        """agent pinned to exactly one provider (like autoparts-assistant):
        resolution must keep the pool worker as the final candidate, and the
        execution of a request must succeed via the pool worker."""
        providers = {"nvidia-nim-nemotron-35-lightning": _nim_provider_config()}
        pool_worker = _healthy_pool_worker()

        with (
            _patch_scripted(return_value=None),
            _patch_store(providers),
            _patch_pool(pool_worker),
        ):
            from api_service.agent.factory import resolve_llm

            result = await resolve_llm(
                provider_priority=["nvidia-nim-nemotron-35-lightning"]
            )

        assert isinstance(result, AnswerNormalizer)
        assert isinstance(result.inner, FallbackProvider), (
            "single pinned provider must not bypass fallback: "
            f"got {type(result.inner).__name__}"
        )
        assert result.inner._providers[-1] is pool_worker, (
            "pool worker must be the final candidate behind the pinned provider"
        )

        # Kill the pinned provider at execution time (live NIM behavior).
        result.inner._providers[0].complete = AsyncMock(
            side_effect=TimeoutError("nvidia_nim upstream timeout")
        )
        response = await result.complete(_request())

        assert response.content == "answer from pool worker"
        assert result.inner.model == "openai/gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_all_dead_named_candidates_still_reach_pool(self):
        """Several named candidates, all dead → the request still succeeds
        through the pool worker."""
        providers = {
            "nvidia-nim-nemotron-35-lightning": _nim_provider_config(),
            "mistral": {
                "model": "mistral/mistral-medium-latest",
                "api_key": "sk-mistral-regression",
                "api_base": "",
                "enabled": True,
                "provider": "mistral",
            },
        }
        pool_worker = _healthy_pool_worker()

        with (
            _patch_scripted(return_value=None),
            _patch_store(providers),
            _patch_pool(pool_worker),
        ):
            from api_service.agent.factory import resolve_llm

            result = await resolve_llm(
                provider_priority=["nvidia-nim-nemotron-35-lightning", "mistral"]
            )

        assert isinstance(result.inner, FallbackProvider)
        assert result.inner._providers[-1] is pool_worker

        for provider in result.inner._providers[:-1]:
            provider.complete = AsyncMock(side_effect=TimeoutError("upstream down"))

        response = await result.complete(_request())

        assert response.content == "answer from pool worker"

    @pytest.mark.asyncio
    async def test_pool_worker_not_kept_when_fallback_switch_off(self):
        """The global fallback switch must still disable the pool as a
        last-resort candidate (behavioral parity with named fallbacks)."""
        providers = {"nvidia-nim-nemotron-35-lightning": _nim_provider_config()}
        pool_worker = _healthy_pool_worker()

        with (
            _patch_scripted(return_value=None),
            _patch_store(providers, fallback_enabled=False),
            _patch_pool(pool_worker),
        ):
            from api_service.agent.factory import resolve_llm

            result = await resolve_llm(
                provider_priority=["nvidia-nim-nemotron-35-lightning"]
            )

        assert isinstance(result.inner, LiteLLMProvider)
        assert result.inner is not pool_worker
