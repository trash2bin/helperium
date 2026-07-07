"""LLM client wrapper for LiteLLM."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import litellm
from litellm import CustomStreamWrapper
from litellm.types.utils import ModelResponse

from demo.settings import settings

logger = logging.getLogger("api_service.agent.llm_client")


# ── Protocol — formal contract for structural subtyping ──────────────────────


@runtime_checkable
class LLMClientProtocol(Protocol):
    """Protocol defining the LLM client interface.

    Any class that provides these methods is structurally compatible
    — no need to inherit or register.  This makes it trivial to
    substitute mocks in tests or swap the implementation entirely
    (e.g. OpenAI direct API, Anthropic direct API) without touching
    callers.
    """

    model: str
    api_base: str | None
    enable_thinking: bool

    def stream_completion(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = True,
    ) -> AsyncIterator[tuple[str | None, dict[str, Any] | None]]:
        """Stream an LLM completion.

        Yields (token, None) for each emitted token and
        (None, final_message) exactly once when the response is complete.

        Implemented as ``async def`` with ``yield`` — this is a regular
        ``def`` in the protocol so Pyright sees it as an async generator
        return type rather than a coroutine.
        """
        ...

    def get_final_message(self, messages: list[dict[str, Any]]) -> AsyncIterator[str]:
        """Non-streaming fallback: yield tokens of the final answer."""
        ...

    last_final_message: dict[str, Any] | None


@dataclass(slots=True)
class LLMResponse:
    """Container for LLM response."""

    role: str
    content: str
    tool_calls: list[dict[str, Any]]
    reasoning_content: str | None
    usage: dict[str, Any] | None


class LLMClient:
    """Handles all interactions with the LLM via LiteLLM."""

    def __init__(
        self,
        model: str,
        api_base: str | None = None,
        timeout: float = 600.0,
        temperature: float = 0.5,
        max_tokens_thinking: int = 4096,
        enable_thinking: bool = False,
    ) -> None:
        """
        Initialize LLM client.

        Args:
            model: Model identifier
            api_base: Base URL for API
            timeout: Request timeout in seconds
            temperature: Model temperature (0-1)
            max_tokens_thinking: Maximum tokens for thinking
            enable_thinking: Whether to enable thinking mode
        """
        self.model: str = model
        self.api_base: str | None = api_base
        self.timeout: float = timeout
        self.temperature: float = temperature
        self.max_tokens_thinking: int = max_tokens_thinking
        self.enable_thinking: bool = enable_thinking
        self.last_final_message: dict[str, Any] | None = None

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

        response = await litellm.acompletion(**kwargs)

        # Проверка на корректный тип данных от LiteLLM
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

        # Проверка на корректный тип данных от LiteLLM
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

        response = await litellm.acompletion(
            model=self.model,
            messages=messages,
            stream=True,
            timeout=self.timeout,
            **extra_params,
        )

        # Проверка на корректный тип данных от LiteLLM
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
    2. Mistral API (if MISTRAL_API_KEY is set globally)
    3. Ollama (default fallback)

    Per-agent config keys:
      provider  — ollama, mistral, openai, anthropic
      api_key   — API key for the provider (set as env var for LiteLLM)
      model     — model name (e.g. qwen2.5:0.5b, mistral/mistral-small)
      api_base  — custom API base URL
      temperature — model temperature (0-2)
      max_tokens  — max tokens in response
      system_prompt — system prompt override (NOT used here, handled by orchestrator)
    """
    if llm_config:
        model_name = llm_config.get("model") or settings.ollama_model
        api_base = llm_config.get("api_base") or settings.ollama_url
        temperature = llm_config.get("temperature") or settings.agent_temperature
        provider = llm_config.get("provider")
        api_key = llm_config.get("api_key")

        # Set API key as env var for LiteLLM
        if api_key and provider:
            env_key_map = {
                "openai": "OPENAI_API_KEY",
                "anthropic": "ANTHROPIC_API_KEY",
                "mistral": "MISTRAL_API_KEY",
            }
            if provider in env_key_map:
                os.environ[env_key_map[provider]] = api_key

        # Determine model prefix based on provider
        if provider == "ollama" and api_base:
            known_prefixes = (
                "ollama/",
                "ollama_chat/",
                "openai/",
                "anthropic/",
                "deepseek/",
            )
            if not model_name.startswith(known_prefixes):
                model_name = f"ollama_chat/{model_name}"
        elif provider == "mistral":
            if not model_name.startswith("mistral/"):
                model_name = f"mistral/{model_name}"
            api_base = None  # LiteLLM handles default
        elif provider == "openai":
            if not model_name.startswith("openai/"):
                model_name = f"openai/{model_name}"

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

    # ── Global defaults (no per-agent config) ──
    # Mistral takes priority if API key exists
    if settings.mistral_api_key:
        model = settings.mistral_model
        if not model.startswith("mistral/"):
            model = f"mistral/{model}"
        api_base = None  # LiteLLM handles Mistral's default API base
    else:
        # Ollama configuration
        model_name = settings.ollama_model
        known_providers = (
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

        if settings.ollama_url and not model_name.startswith(known_providers):
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
