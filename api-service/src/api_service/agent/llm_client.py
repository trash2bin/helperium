"""LLM client wrapper for LiteLLM — DEPRECATED.

This module is kept for backward compatibility.  New code should use:

- ``LiteLLMProvider`` from ``.litellm_provider`` for direct LLM calls.
- ``ProviderPool`` from ``.provider_pool`` for health check + failover.
- ``LLMProvider`` protocol from ``.protocols`` for type annotations.

Key replacements:

- ``LLMClientProtocol`` → alias to ``LLMProvider``
- ``LLMClient`` → still functional but emits ``DeprecationWarning``
- ``create_fallback_client()`` → ``ProviderPool.complete_with_fallback()``
- ``_build_router_client()`` → still used by ``create_fallback_client`` internally
"""

from __future__ import annotations

import logging
import os
import time
import warnings
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from api_service.prometheus_metrics import (
    llm_calls_total,
    llm_duration_ms,
    llm_token_usage,
    llm_cost_total,
)
from api_service.pricing import get_model_cost

import litellm
from litellm import CustomStreamWrapper
from litellm.types.utils import ModelResponse

from helperium_sdk.settings import settings

from .protocols import LLMProvider

logger = logging.getLogger("api_service.agent.llm_client")


# ── Re-export LLMProvider as LLMClientProtocol for backward compat ──────────

LLMClientProtocol = LLMProvider
"""Alias for ``LLMProvider`` protocol.

Kept for backward compatibility with existing handlers.
"""


@dataclass(slots=True)
class LLMResponse:
    """Container for LLM response."""

    role: str
    content: str
    tool_calls: list[dict[str, Any]]
    reasoning_content: str | None
    usage: dict[str, Any] | None


class LLMClient:
    """Handles all interactions with the LLM via LiteLLM.

    **Deprecated.**  New code should use ``LiteLLMProvider`` instead.
    Kept for backward compatibility with existing tests and handlers.

    Supports both direct ``litellm.acompletion()`` and
    ``litellm.Router.acompletion()`` when a router is provided.
    """

    def __init__(
        self,
        model: str,
        api_base: str | None = None,
        timeout: float = 600.0,
        temperature: float = 0.5,
        max_tokens_thinking: int = 4096,
        enable_thinking: bool = False,
        router: Any = None,
        router_group: str | None = None,
    ) -> None:
        warnings.warn(
            "LLMClient is deprecated; use LiteLLMProvider instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        self.model: str = model
        self.api_base: str | None = api_base
        self.timeout: float = timeout
        self.temperature: float = temperature
        self.max_tokens_thinking: int = max_tokens_thinking
        self.enable_thinking: bool = enable_thinking
        self.router: Any = router
        self.router_group: str | None = router_group
        self.last_final_message: dict[str, Any] | None = None
        self.last_usage: dict[str, int] | None = None
        self.last_cost: float = 0.0

    def _get_extra_params(self) -> dict[str, Any]:
        """Get extra parameters for LiteLLM completion calls."""
        extra_params: dict[str, Any] = {}
        if self.enable_thinking:
            extra_params["extra_body"] = {"think": True}
        if self.api_base:
            extra_params["api_base"] = self.api_base
        return extra_params

    async def stream_completion(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = True,
        tenant_ids: list[str] | None = None,
    ) -> AsyncIterator[tuple[str | None, dict[str, Any] | None]]:
        """
        Stream LLM completion and yield (token, final_message) tuples.

        Yields:
            - (token, None) for each token
            - (None, final_message) when final message is ready

        Args:
            messages: List of message dicts
            tools: Optional list of tool definitions
            stream: Whether to stream tokens
            tenant_ids: Optional tenant IDs for cost attribution
        """
        extra_params = self._get_extra_params()

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "timeout": self.timeout,
            "temperature": self.temperature,
            **extra_params,
        }

        if tools:
            kwargs["tools"] = tools
        if stream:
            kwargs["max_tokens"] = self.max_tokens_thinking
            kwargs["stream"] = True

        _start_time = time.monotonic()
        if self.router is not None:
            if self.router_group:
                kwargs["model_group"] = self.router_group
            response = await self.router.acompletion(  # type: ignore[arg-type, call-overload]
                **kwargs
            )
        else:
            response = await litellm.acompletion(**kwargs)
        _duration_ms = (time.monotonic() - _start_time) * 1000
        llm_duration_ms.labels(model=self.model).observe(_duration_ms)
        llm_calls_total.labels(
            model=self.model,
            provider=self.model.split("/")[0] if "/" in self.model else "unknown",
        ).inc()

        if not isinstance(response, CustomStreamWrapper):
            logger.error(
                "Expected CustomStreamWrapper, got %s",
                type(response).__name__,
            )
            raise TypeError(
                f"Expected CustomStreamWrapper, got {type(response).__name__}"
            )

        chunks: list[Any] = []
        async for chunk in response:
            chunks.append(chunk)
            delta = chunk.choices[0].delta
            token: str | None = getattr(delta, "content", None)
            if token:
                yield (token, None)

        final = litellm.stream_chunk_builder(chunks, messages=messages)
        self._validate_final_response(final)

        if final is None:
            raise RuntimeError("stream_chunk_builder returned None")
        elif not isinstance(final, ModelResponse):
            logger.error(
                "Expected ModelResponse, got %s",
                type(final).__name__,
            )
            raise TypeError(f"Expected ModelResponse, got {type(final).__name__}")

        msg_obj = final.choices[0].message
        if msg_obj is None:
            raise RuntimeError("ModelResponse.choices[0].message is None")

        result: dict[str, Any] = self._build_response_dict(msg_obj)
        self.last_final_message = result

        # Track token usage
        final_usage_obj = getattr(final, "usage", None)
        if final_usage_obj:
            pt = getattr(final_usage_obj, "prompt_tokens", 0) or 0
            ct = getattr(final_usage_obj, "completion_tokens", 0) or 0
            tt = getattr(final_usage_obj, "total_tokens", 0) or 0
            if pt:
                llm_token_usage.labels(type="prompt").inc(pt)
            if ct:
                llm_token_usage.labels(type="completion").inc(ct)
            if tt:
                llm_token_usage.labels(type="total").inc(tt)
            # Realistic cost from pricing table
            cost = get_model_cost(self.model, pt, ct)
            self.last_cost = cost if cost is not None else 0.0
            self.last_usage = {
                "prompt_tokens": pt,
                "completion_tokens": ct,
                "total_tokens": tt,
            }
            if cost is not None and cost > 0:
                tid = tenant_ids[0] if tenant_ids else "unknown"
                prov = self.model.split("/")[0] if "/" in self.model else "unknown"
                llm_cost_total.labels(
                    model=self.model,
                    provider=prov,
                    tenant_id=tid,
                ).inc(cost)

        # Log reasoning if present
        if result.get("reasoning_content"):
            logger.info("[LLM][REASONING]\n%s", result["reasoning_content"])
        else:
            logger.warning("[LLM] reasoning_content is empty")

        yield (None, result)

    async def get_final_message(
        self, messages: list[dict[str, Any]]
    ) -> AsyncIterator[str]:
        """Get final message tokens without streaming intermediate tokens."""
        extra_params = self._get_extra_params()

        if self.router is not None:
            kwargs_router: dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                "stream": True,
                "timeout": self.timeout,
                **extra_params,
            }
            if self.router_group:
                kwargs_router["model_group"] = self.router_group
            response = await self.router.acompletion(  # type: ignore[call-overload, reportArgumentType]
                **kwargs_router
            )
        else:
            response = await litellm.acompletion(  # type: ignore[reportArgumentType]
                model=self.model,
                messages=messages,
                stream=True,
                timeout=self.timeout,
                **extra_params,
            )

        if not isinstance(response, CustomStreamWrapper):
            logger.error(
                "Expected CustomStreamWrapper, got %s",
                type(response).__name__,
            )
            raise TypeError(
                f"Expected CustomStreamWrapper, got {type(response).__name__}"
            )

        async for chunk in response:
            token = chunk.choices[0].delta.content
            if isinstance(token, str) and token:
                yield token

    def _validate_final_response(self, final: Any) -> None:
        """Validate final response type."""
        if final is None:
            raise RuntimeError("stream_chunk_builder returned None")
        if not isinstance(final, ModelResponse):
            error_msg = f"Expected ModelResponse, got {type(final).__name__}"
            logger.error(error_msg)
            raise TypeError(error_msg)

    def _build_response_dict(self, msg_obj: Any) -> dict[str, Any]:
        """Build response dictionary from message object."""
        result: dict[str, Any] = {
            "role": msg_obj.role or "assistant",
            "content": msg_obj.content or "",
        }

        # Add tool calls if present
        tool_calls = msg_obj.tool_calls or []
        if tool_calls:
            result["tool_calls"] = [
                {
                    "id": getattr(tc, "id", None),
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments or "{}",
                    },
                }
                for tc in tool_calls
            ]

        # Add reasoning content if present
        reasoning = getattr(msg_obj, "reasoning_content", None)
        if reasoning:
            result["reasoning_content"] = reasoning

        return result


def create_client(llm_config: dict | None = None) -> LLMClient:
    """
    Factory function to create LLM client based on environment or per-agent settings.

    Priority order:
    1. Per-agent llm_config (if provided)
    2. Env vars: first *{PREFIX}_API_KEY* + *{PREFIX}_MODEL* found
    3. Ollama (default fallback via OLLAMA_URL / OLLAMA_MODEL)

    Per-agent config keys:
      provider  — ollama, mistral, openai, anthropic, groq, ... (любой LiteLLM)
      api_key   — API key for the provider (set as env var for LiteLLM)
      model     — model name (e.g. qwen2.5:0.5b, mistral/mistral-small)
      api_base  — custom API base URL
      temperature — model temperature (0-2)
      max_tokens  — max tokens in response
      system_prompt — system prompt override (NOT used here, handled by orchestrator)
    """
    if llm_config:
        return _create_from_llm_config(llm_config)

    # ── Global defaults (no per-agent config) ──────────────────────────
    # 1. Scan env for *{PREFIX}_API_KEY* + *{PREFIX}_MODEL*
    client = _create_from_env_fallback()
    if client is not None:
        return client

    # 2. Ollama (default)
    return _create_ollama_client()


# ── Helper factories for create_client ─────────────────────────────────


def _create_from_llm_config(llm_config: dict) -> LLMClient:
    """Создаёт LLMClient из per-agent конфига."""
    model_name = llm_config.get("model") or settings.ollama_model
    api_base = llm_config.get("api_base") or settings.ollama_url
    temperature = llm_config.get("temperature") or settings.agent_temperature
    provider = llm_config.get("provider")
    api_key = llm_config.get("api_key")

    if api_key and provider:
        _set_provider_env_key(provider, api_key)

    model_name = _prefix_model(provider, model_name, api_base)

    # Provider-specific overrides
    if provider == "mistral":
        api_base = None  # LiteLLM handles Mistral's default

    api_base_url = api_base.rstrip("/") if api_base else None

    return LLMClient(
        model=model_name,
        api_base=api_base_url,
        timeout=settings.request_timeout,
        temperature=temperature,
        max_tokens_thinking=llm_config.get("max_tokens")
        or settings.agent_max_tokens_thinking,
        enable_thinking=settings.think_mode,
    )


def _create_from_env_fallback() -> LLMClient | None:
    """Сканирует os.environ на *{PREFIX}_API_KEY* + *{PREFIX}_MODEL*.

    Возвращает LLMClient для первого найденного совпадения или None.
    """
    for key, val in os.environ.items():
        if not key.endswith("_API_KEY") or not val:
            continue
        prefix = key.removesuffix("_API_KEY")
        if not prefix:
            continue
        model = os.environ.get(f"{prefix}_MODEL", "")
        if not model:
            continue
        # LiteLLM expects prefix/model format
        provider_slug = prefix.lower()
        if provider_slug == "mistral" and not model.startswith("mistral/"):
            model = f"mistral/{model}"
        elif provider_slug == "openai" and not model.startswith("openai/"):
            model = f"openai/{model}"
        elif provider_slug == "anthropic" and not model.startswith("anthropic/"):
            model = f"anthropic/{model}"
        api_base = os.environ.get(f"{prefix}_API_BASE", "") or None

        return LLMClient(
            model=model,
            api_base=api_base,
            timeout=settings.request_timeout,
            temperature=settings.agent_temperature,
            max_tokens_thinking=settings.agent_max_tokens_thinking,
            enable_thinking=settings.think_mode,
        )
    return None


def _create_ollama_client() -> LLMClient:
    """Создаёт LLMClient для Ollama (локальная модель)."""
    model_name = settings.ollama_model
    known_prefixes = (
        "ollama/",
        "ollama_chat/",
        "openai/",
        "anthropic/",
        "deepseek/",
        "huggingface/",
        "mistral/",
        "groq/",
        "together_ai/",
    )
    if settings.ollama_url and not model_name.startswith(known_prefixes):
        model = f"ollama_chat/{model_name}"
    else:
        model = model_name
    api_base = settings.ollama_url.rstrip("/") if settings.ollama_url else None

    return LLMClient(
        model=model,
        api_base=api_base,
        timeout=settings.request_timeout,
        temperature=settings.agent_temperature,
        max_tokens_thinking=settings.agent_max_tokens_thinking,
        enable_thinking=settings.think_mode,
    )


def _set_provider_env_key(provider: str, api_key: str) -> None:
    """Устанавливает env var для провайдера (LiteLLM смотрит в os.environ)."""
    key_map = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "mistral": "MISTRAL_API_KEY",
    }
    if provider in key_map:
        os.environ[key_map[provider]] = api_key


def _prefix_model(provider: str | None, model_name: str, api_base: str | None) -> str:
    """Добавляет префикс провайдера к модели, если нужно."""
    if provider == "ollama" and api_base:
        known_prefixes = (
            "ollama/",
            "ollama_chat/",
            "openai/",
            "anthropic/",
            "deepseek/",
        )
        if not model_name.startswith(known_prefixes):
            return f"ollama_chat/{model_name}"
    elif provider == "mistral":
        if not model_name.startswith("mistral/"):
            return f"mistral/{model_name}"
    elif provider == "openai":
        if not model_name.startswith("openai/"):
            return f"openai/{model_name}"
    return model_name


# ── DEPRECATED factories — kept for backward compat ────────────────────────


def create_fallback_client() -> LLMClient:
    """Create an LLM client with Router for global provider failover.

    **Deprecated.**  Use ``ProviderPool.complete_with_fallback()`` instead.

    Reads all enabled providers from ProviderStore and creates a ``litellm.Router``
    so subsequent providers are tried when the first one fails.

    Unlike the old behaviour (one Router at startup), this can be called on every
    request so the Router always reflects the current ProviderStore state.
    """
    from api_service.provider_store import get_provider_store

    warnings.warn(
        "create_fallback_client is deprecated; use ProviderPool instead.",
        DeprecationWarning,
        stacklevel=2,
    )

    store = get_provider_store()
    model_list = store.get_active_router_config()

    if not model_list:
        logger.info("[FALLBACK] No active providers in store — using default client")
        return create_client()

    GROUP_NAME = "fallback_group"
    return _build_router_client(model_list, GROUP_NAME, log_prefix="FALLBACK")


def _build_router_client(
    model_list: list[dict[str, Any]],
    group_name: str,
    log_prefix: str = "FALLBACK",
) -> LLMClient:
    """Create an LLMClient backed by a litellm.Router from a prepared model_list.

    **Deprecated.**  Internal helper for ``create_fallback_client`` and
    ``create_prioritized_client``.
    """
    from litellm.router import Router

    for entry in model_list:
        entry["model_name"] = group_name

    router = Router(
        model_list=model_list,
        num_retries=1,
        set_verbose=False,
    )

    logger.info(
        "[%s] Created Router group='%s' with %d providers: %s",
        log_prefix,
        group_name,
        len(model_list),
        [m["litellm_params"]["model"] for m in model_list],
    )

    primary_model = model_list[0]["litellm_params"]["model"]
    primary_api_base = model_list[0]["litellm_params"].get("api_base")

    return LLMClient(
        model=primary_model,
        api_base=primary_api_base,
        router=router,
        router_group=group_name,
        timeout=settings.request_timeout,
        temperature=settings.agent_temperature,
        max_tokens_thinking=settings.agent_max_tokens_thinking,
        enable_thinking=settings.think_mode,
    )
