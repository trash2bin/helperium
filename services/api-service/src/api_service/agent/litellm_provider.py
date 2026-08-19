"""LiteLLM transport for the minimal typed provider protocol."""

from __future__ import annotations

import json
from typing import Any

import litellm
from litellm.types.utils import ModelResponse

from .models import CompletionRequest, CompletionResponse, ToolCall, UsageInfo


class ProviderProtocolError(ValueError):
    """The provider returned a response that cannot be represented safely."""


class LiteLLMProvider:
    """Translate native LiteLLM function calls into the one provider protocol.

    Text is final assistant text. The adapter deliberately does not parse JSON,
    XML, Markdown or provider-specific delimiters from ``content`` as tool calls.
    """

    def __init__(
        self,
        model: str,
        api_base: str | None = None,
        api_key: str | None = None,
        timeout: float = 120.0,
        temperature: float = 0.2,
        max_tokens_thinking: int = 0,
        enable_thinking: bool = False,
    ) -> None:
        self.model = model
        self.api_base = api_base
        self.api_key = api_key
        self.timeout = timeout
        self.temperature = temperature
        self.max_tokens_thinking = max_tokens_thinking
        self.enable_thinking = enable_thinking

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": request.messages,
            "temperature": self.temperature,
            "timeout": self.timeout,
        }
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.enable_thinking:
            kwargs["extra_body"] = {"think": True}
        if request.tools:
            kwargs["tools"] = request.tools

        response = await litellm.acompletion(**kwargs)
        if not isinstance(response, ModelResponse):
            raise ProviderProtocolError(
                f"LiteLLM returned {type(response).__name__}, expected ModelResponse"
            )
        if not response.choices:
            raise ProviderProtocolError("LiteLLM returned no choices")

        message = response.choices[0].message
        tool_calls = [self._tool_call(raw) for raw in (message.tool_calls or [])]
        usage = self._usage(getattr(response, "usage", None))
        return CompletionResponse(
            content=message.content or "",
            tool_calls=tool_calls,
            usage=usage,
            cost=self._cost(response, getattr(response, "usage", None)),
        )

    @staticmethod
    def _tool_call(raw: Any) -> ToolCall:
        function = getattr(raw, "function", None)
        call_id = getattr(raw, "id", None)
        name = getattr(function, "name", None)
        raw_arguments = getattr(function, "arguments", None)
        if not isinstance(call_id, str) or not call_id:
            raise ProviderProtocolError("native tool call has no id")
        if not isinstance(name, str) or not name:
            raise ProviderProtocolError("native tool call has no function name")
        if isinstance(raw_arguments, str):
            try:
                arguments = json.loads(raw_arguments)
            except json.JSONDecodeError as exc:
                raise ProviderProtocolError(
                    f"native tool call '{name}' has invalid JSON arguments"
                ) from exc
        else:
            arguments = raw_arguments
        if not isinstance(arguments, dict):
            raise ProviderProtocolError(
                f"native tool call '{name}' arguments must be a JSON object"
            )
        return ToolCall(id=call_id, name=name, arguments=arguments)

    @staticmethod
    def _usage(raw: Any) -> UsageInfo | None:
        if raw is None:
            return None
        return UsageInfo(
            prompt_tokens=getattr(raw, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(raw, "completion_tokens", 0) or 0,
            total_tokens=getattr(raw, "total_tokens", 0) or 0,
        )

    @staticmethod
    def _cost(response: ModelResponse, usage: Any) -> float:
        if usage is not None:
            reported = getattr(usage, "cost", None)
            if isinstance(reported, (int, float)) and reported >= 0:
                return float(reported)
        hidden = getattr(response, "_hidden_params", None)
        if isinstance(hidden, dict):
            reported = hidden.get("cost")
            if isinstance(reported, (int, float)) and reported >= 0:
                return float(reported)
        return 0.0
