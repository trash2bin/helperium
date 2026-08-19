"""LLM provider resolution — factory functions and helpers.

Responsibilities
----------------
* ``_create_env_provider()`` — build a ``LiteLLMProvider`` from OS env vars.
* ``_resolve_pool_or_env()`` — try ``ProviderPool`` first, fall back to env.
* ``_prefix_model()`` — add a provider prefix to a model name when needed.
* ``resolve_llm()`` — the top-level resolver that picks the right provider
  for a given request (scripted, llm_client, llm_config, provider_priority,
  pool/env fallback).

API keys are passed directly to ``LiteLLMProvider(api_key=...)`` rather than
injected into ``os.environ``, which avoids a process-global race condition
under concurrent requests.
"""

from __future__ import annotations

import os
import warnings
from typing import Any

from helperium_sdk.settings import settings

from .litellm_provider import LiteLLMProvider
from .provider_pool import ProviderPool


# ── Provider prefix mapping (slug → LiteLLM prefix) ──────────────────

_PROVIDER_PREFIXES: dict[str, str] = {
    "ollama": "ollama_chat/",
    "mistral": "mistral/",
    "openai": "openai/",
    "anthropic": "anthropic/",
    "deepseek": "deepseek/",
    "huggingface": "huggingface/",
    "groq": "groq/",
    "together_ai": "together_ai/",
}
# Quick-check: all known prefixes (values of the dict above)
_KNOWN_PREFIXES = tuple(_PROVIDER_PREFIXES.values())

# Module-level ProviderPool singleton for fallback + health checks.
_pool = ProviderPool()


# ── Low-level helpers ───────────────────────────────────────────────────


def _create_env_provider() -> LiteLLMProvider:
    """Create a LiteLLMProvider from environment variables (last-resort fallback)."""

    # 1. Scan env for *{PREFIX}_API_KEY* + *{PREFIX}_MODEL*
    for key, val in os.environ.items():
        if not key.endswith("_API_KEY") or not val:
            continue
        prefix = key.removesuffix("_API_KEY")
        if not prefix:
            continue
        model = os.environ.get(f"{prefix}_MODEL", "")
        if not model:
            continue
        provider_slug = prefix.lower()
        known_prefix = _PROVIDER_PREFIXES.get(provider_slug)
        if known_prefix and not model.startswith(known_prefix):
            model = f"{known_prefix}{model}"
        api_base = os.environ.get(f"{prefix}_API_BASE", "") or None
        return LiteLLMProvider(
            model=model,
            api_base=api_base,
            timeout=settings.request_timeout,
            temperature=settings.agent_temperature,
            max_tokens_thinking=settings.agent_max_tokens_thinking,
            enable_thinking=settings.think_mode,
        )

    # 2. Fall back to Ollama
    model_name = settings.ollama_model
    if settings.ollama_url and not model_name.startswith(_KNOWN_PREFIXES):
        model_name = f"ollama_chat/{model_name}"
    api_base = settings.ollama_url.rstrip("/") if settings.ollama_url else None
    return LiteLLMProvider(
        model=model_name,
        api_base=api_base,
        timeout=settings.request_timeout,
        temperature=settings.agent_temperature,
        max_tokens_thinking=settings.agent_max_tokens_thinking,
        enable_thinking=settings.think_mode,
    )


async def _resolve_pool_or_env() -> LiteLLMProvider:
    """Try ProviderPool first, then fall back to env-based provider."""
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


def _prefix_model(provider: str | None, model_name: str, api_base: str | None) -> str:
    """Add provider prefix to model name if needed."""
    if not provider:
        return model_name
    # Ollama only gets the chat transport prefix when using a custom API base.
    # ``ollama/...`` is already a canonical LiteLLM model identifier; adding
    # ``ollama_chat/`` to it produces the invalid double prefix
    # ``ollama_chat/ollama/...``.
    if provider == "ollama":
        if api_base and not model_name.startswith(("ollama/", "ollama_chat/")):
            return f"ollama_chat/{model_name}"
        return model_name
    prefix = _PROVIDER_PREFIXES.get(provider, "")
    if prefix and not model_name.startswith(_KNOWN_PREFIXES):
        return f"{prefix}{model_name}"
    return model_name


# ── Top-level resolver ──────────────────────────────────────────────────


async def resolve_llm(
    *,
    llm_client: Any | None = None,
    llm_config: dict | None = None,
    provider_priority: list[str] | None = None,
    _test_llm_client: Any | None = None,
) -> Any:
    """Resolve the LLM provider for a single request.

    Resolution order:

    1. **Scripted** — ``USE_SCRIPTED_LLM=1`` dev mode; deterministic.
    2. **llm_client** — explicit provider passed by the caller.
    3. **llm_config** — per-request config dict (model, api_base, provider, api_key).
    4. **provider_priority** — ordered list of provider names; first valid wins.
    5. **Pool / env fallback** — ``ProviderPool`` first, then env-based provider.

    Returns:
        An LLM provider instance (usually ``LiteLLMProvider``).
    """
    from .scripted_provider import create_scripted_provider as _create_scripted

    # 1. Scripted — deterministic dev/testing provider
    scripted = _create_scripted()
    if scripted:
        return scripted

    # 2. Explicit llm_client (e.g. injected by tests or prioritized caller)
    if llm_client:
        return llm_client

    # 3. Per-request llm_config dict
    if llm_config:
        model_name = llm_config.get("model") or settings.ollama_model
        api_base = llm_config.get("api_base") or settings.ollama_url
        provider = llm_config.get("provider")
        api_key = llm_config.get("api_key")

        model_name = _prefix_model(provider, model_name, api_base)
        if provider == "mistral":
            api_base = None
        api_base_url = api_base.rstrip("/") if api_base else None

        return LiteLLMProvider(
            model=model_name,
            api_base=api_base_url,
            api_key=api_key,
            timeout=settings.request_timeout,
            temperature=llm_config.get("temperature") or settings.agent_temperature,
            max_tokens_thinking=llm_config.get("max_tokens")
            or settings.agent_max_tokens_thinking,
            enable_thinking=settings.think_mode,
            tools_after_tool_result=llm_config.get("tools_after_tool_result", True),
        )

    # 4. Provider priority list
    if provider_priority:
        from api_service.provider_store import (
            KNOWN_PROVIDERS as _KNOWN,
            get_provider_store,
        )

        store = get_provider_store()
        raw_providers = store.all_providers_raw
        found = None
        for name in provider_priority:
            provider_data = raw_providers.get(name)
            if not provider_data:
                continue
            if not provider_data.get("enabled", True):
                continue
            model = provider_data.get("model", "")
            if not model:
                continue
            found = (name, provider_data)
            break

        if found:
            name, data = found
            model = data["model"]
            provider = data.get("provider", "")
            if not model.startswith(tuple(p + "/" for p in _KNOWN)) and provider:
                model = f"{provider}/{model}"
            api_base = data.get("api_base", "") or ""
            return LiteLLMProvider(
                model=model,
                api_base=api_base or None,
                api_key=data.get("api_key") or None,
                timeout=120.0,
            )
        else:
            # Fallback: try ProviderPool, then env
            return _test_llm_client or await _resolve_pool_or_env()

    # 5. Pool / env fallback — FRESH provider every request
    return _test_llm_client or await _resolve_pool_or_env()
