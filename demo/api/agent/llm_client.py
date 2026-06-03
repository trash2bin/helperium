"""LLM client wrapper for LiteLLM."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import litellm
from litellm import CustomStreamWrapper
from litellm.types.utils import ModelResponse

from demo.settings import settings

logger = logging.getLogger("demo.api.agent.llm_client")


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
        self._last_final_message: dict[str, Any] | None = None

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

        self._validate_response(response)

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
            raise TypeError(
                f"Expected ModelResponse, got {type(final).__name__}"
            )

        msg_obj = final.choices[0].message
        if msg_obj is None:
            raise RuntimeError("ModelResponse.choices[0].message is None")

        result: dict[str, Any] = self._build_response_dict(msg_obj)
        self._last_final_message = result

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

        self._validate_response(response)

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

    def _validate_response(self, response: Any) -> None:
        """Validate response type."""
        if not isinstance(response, CustomStreamWrapper):
            error_msg = f"Expected CustomStreamWrapper, got {type(response).__name__}"
            logger.error(error_msg)
            raise TypeError(error_msg)

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

    @property
    def last_final_message(self) -> dict[str, Any] | None:
        """Get the last final message (for fallback logic)."""
        return self._last_final_message
