"""LLM provider resolution.

This module selects a configured provider.  It deliberately does not rewrite
model identifiers or implement provider transport conventions: those belong to
``LiteLLMProvider``, the single adapter around LiteLLM.
"""

from __future__ import annotations

import os
import warnings
from typing import Any

from helperium_sdk.settings import settings

from .litellm_provider import LiteLLMProvider
from .provider_pool import FallbackProvider, ProviderPool


# Module-level ProviderPool singleton for fallback + health checks.
_pool = ProviderPool()


def _create_env_provider() -> LiteLLMProvider:
    """Create a provider from environment variables as a last-resort fallback."""
    for key, value in os.environ.items():
        if not key.endswith("_API_KEY") or not value:
            continue
        prefix = key.removesuffix("_API_KEY")
        if not prefix:
            continue
        model = os.environ.get(f"{prefix}_MODEL", "")
        if not model:
            continue
        return LiteLLMProvider(
            model=model,
            provider=prefix.lower(),
            api_base=os.environ.get(f"{prefix}_API_BASE", "") or None,
            timeout=settings.request_timeout,
            temperature=settings.agent_temperature,
            max_tokens_thinking=settings.agent_max_tokens_thinking,
            enable_thinking=settings.think_mode,
        )

    return LiteLLMProvider(
        model=settings.ollama_model,
        provider="ollama",
        api_base=settings.ollama_url.rstrip("/") if settings.ollama_url else None,
        timeout=settings.request_timeout,
        temperature=settings.agent_temperature,
        max_tokens_thinking=settings.agent_max_tokens_thinking,
        enable_thinking=settings.think_mode,
    )


async def _resolve_pool_or_env() -> LiteLLMProvider:
    """Try ProviderPool first, then build an environment fallback."""
    try:
        worker = await _pool.get_any_worker()
        if worker is not None:
            return worker
    except Exception:
        pass
    warnings.warn(
        "ProviderPool is empty or unavailable — falling back to env-based LiteLLMProvider",
        RuntimeWarning,
        stacklevel=2,
    )
    return _create_env_provider()


def _create_configured_provider(
    config: dict, *, strip_api_base: bool = False
) -> LiteLLMProvider:
    """Build one LiteLLM transport from a persisted or per-agent config."""
    model = config.get("model") or settings.ollama_model
    api_base = config.get("api_base") or settings.ollama_url
    return LiteLLMProvider(
        model=model,
        provider=config.get("provider"),
        api_base=api_base.rstrip("/")
        if strip_api_base and api_base
        else api_base or None,
        api_key=config.get("api_key"),
        timeout=config.get("timeout") or settings.request_timeout,
        temperature=config.get("temperature") or settings.agent_temperature,
        max_tokens_thinking=config.get("max_tokens")
        or settings.agent_max_tokens_thinking,
        enable_thinking=settings.think_mode,
    )


def _provider_identity(config: dict) -> tuple[str, str, str]:
    """Return a credential-free identity used to remove duplicate candidates."""
    return (
        config.get("provider") or "",
        config.get("model") or settings.ollama_model,
        (config.get("api_base") or settings.ollama_url or "").rstrip("/"),
    )


async def resolve_llm(
    *,
    llm_client: Any | None = None,
    llm_config: dict | None = None,
    provider_priority: list[str] | None = None,
    _test_llm_client: Any | None = None,
) -> Any:
    """Resolve the provider for one request.

    Resolution order is scripted development provider, explicitly injected
    client, per-agent configuration, named provider priority, then pool/env.
    The factory owns selection only; LiteLLM owns model/provider routing.
    """
    from .scripted_provider import create_scripted_provider as _create_scripted

    scripted = _create_scripted()
    if scripted:
        return scripted

    if llm_client:
        return llm_client

    candidates: list[LiteLLMProvider] = []
    candidate_identities: set[tuple[str, str, str]] = set()

    if llm_config:
        candidates.append(_create_configured_provider(llm_config, strip_api_base=True))
        candidate_identities.add(_provider_identity(llm_config))

    fallback_enabled = True
    if provider_priority:
        from api_service.provider_store import get_provider_store

        store = get_provider_store()
        fallback_enabled = store.get_fallback_enabled()
        raw_providers = store.all_providers_raw
        for name in provider_priority:
            data = raw_providers.get(name)
            if not data or not data.get("enabled", True) or not data.get("model"):
                continue
            identity = _provider_identity(data)
            if identity in candidate_identities:
                continue
            candidates.append(_create_configured_provider(data))
            candidate_identities.add(identity)

    if candidates:
        if fallback_enabled and len(candidates) > 1:
            return FallbackProvider(candidates)
        return candidates[0]

    return _test_llm_client or await _resolve_pool_or_env()
