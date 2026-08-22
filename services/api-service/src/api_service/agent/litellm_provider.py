"""LiteLLM transport for the minimal typed provider protocol."""

from __future__ import annotations

import json
import logging
from typing import Any

import litellm
from litellm.types.utils import ModelResponse

from helperium_sdk.settings import settings
from api_service.prometheus_metrics import (
    llm_completion_attempts_total,
    llm_retries_total,
    llm_retry_delay_seconds,
    llm_retry_exhausted_total,
    llm_retry_suppressed_total,
)

from .completion_retry import CompletionRetryExecutor, CompletionRetryPolicy
from .models import CompletionRequest, CompletionResponse, ToolCall, UsageInfo
from .provider_compatibility import find_provider_model_policy


logger = logging.getLogger("api_service.agent.litellm_provider")


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
        provider: str | None = None,
        api_base: str | None = None,
        api_key: str | None = None,
        timeout: float = 120.0,
        temperature: float = 0.2,
        max_tokens_thinking: int = 0,
        enable_thinking: bool = False,
        retry_executor: CompletionRetryExecutor | None = None,
    ) -> None:
        self.model = model
        self.provider = provider or None
        self.api_base = api_base
        self.api_key = api_key
        self.timeout = timeout
        self.temperature = temperature
        self.max_tokens_thinking = max_tokens_thinking
        self.enable_thinking = enable_thinking
        self._policy = find_provider_model_policy(self.provider, self.model)
        self._retry_executor = retry_executor or CompletionRetryExecutor(
            CompletionRetryPolicy(
                max_attempts=settings.llm_max_attempts,
                max_elapsed_seconds=settings.llm_retry_max_elapsed_seconds,
                transient_base_seconds=settings.llm_retry_transient_base_seconds,
                throttled_base_seconds=settings.llm_retry_throttled_base_seconds,
                max_backoff_seconds=settings.llm_retry_max_backoff_seconds,
            )
        )

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        messages = self._serialize_transcript(request.messages)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "timeout": self.timeout,
        }
        if self.provider:
            kwargs["custom_llm_provider"] = self.provider
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if self.api_key:
            kwargs["api_key"] = self.api_key
        extra_body = self._thinking_extra_body()
        if extra_body:
            kwargs["extra_body"] = extra_body
        tools, continuation, supports_function_calling = self._completion_tools(request)
        if tools:
            kwargs["tools"] = tools
        logger.info(
            "[LLM] completion policy model=%s provider=%s tools_sent=%s "
            "tool_count=%d current_tool_continuation=%s "
            "supports_function_calling=%s",
            self.model,
            self.provider or "(inferred)",
            bool(tools),
            len(tools),
            continuation,
            supports_function_calling,
        )

        provider_label = self.provider or "inferred"

        async def completion_attempt(attempt_timeout: float) -> Any:
            attempt_kwargs = {**kwargs, "timeout": attempt_timeout}
            return await litellm.acompletion(**attempt_kwargs)

        response = await self._retry_executor.run(
            completion_attempt,
            provider_timeout=self.timeout,
            model=self.model,
            provider=self.provider,
            on_attempt=lambda: self._record_retry_attempt(provider_label),
            on_retry=lambda category, delay: self._record_retry_delay(
                provider_label, category, delay
            ),
            on_exhausted=lambda category, reason: self._record_retry_exhausted(
                provider_label, category, reason
            ),
            on_suppressed=lambda reason: self._record_retry_suppressed(
                provider_label, reason
            ),
        )
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

    def _record_retry_attempt(self, provider: str) -> None:
        llm_completion_attempts_total.labels(self.model, provider).inc()

    def _record_retry_suppressed(self, provider: str, reason: str) -> None:
        llm_retry_suppressed_total.labels(self.model, provider, reason).inc()

    def _record_retry_delay(self, provider: str, category: str, delay: float) -> None:
        llm_retries_total.labels(self.model, provider, category).inc()
        llm_retry_delay_seconds.labels(self.model, provider, category).observe(delay)

    def _record_retry_exhausted(
        self, provider: str, category: str, reason: str
    ) -> None:
        llm_retry_exhausted_total.labels(self.model, provider, category, reason).inc()

    def _thinking_extra_body(self) -> dict[str, Any] | None:
        """Return verified model-specific reasoning controls when available."""
        if self._policy is not None and self._policy.reasoning_body is not None:
            return self._policy.reasoning_body(self.enable_thinking)
        if self.provider != "nvidia_nim":
            return {"think": True} if self.enable_thinking else None
        return None

    def _completion_tools(
        self, request: CompletionRequest
    ) -> tuple[list[dict[str, Any]], bool, bool | None]:
        """Return schemas and the explicit current-turn policy decision.

        LiteLLM owns provider/model capability metadata, but only the agent can
        distinguish a fresh user turn from the immediate continuation of its own
        tool cycle. Historical ``role: tool`` records are replayed for context
        and must never suppress schemas for a new user request.
        """
        continuation = self._is_unresolved_tool_continuation(request.messages)
        if not request.tools or not continuation:
            return request.tools, continuation, None
        if self._policy is not None and self._policy.keep_tool_schemas_on_continuation:
            return request.tools, continuation, None
        try:
            supports_function_calling = litellm.supports_function_calling(
                self.model, custom_llm_provider=self.provider
            )
        except Exception:
            return request.tools, continuation, None
        return (
            request.tools if supports_function_calling else [],
            continuation,
            supports_function_calling,
        )

    @staticmethod
    def _is_unresolved_tool_continuation(messages: list[dict[str, Any]]) -> bool:
        """Whether the current completion immediately follows an open tool cycle."""
        if not messages:
            return False
        last = messages[-1]
        if last.get("role") == "tool":
            return True
        return last.get("role") == "assistant" and bool(last.get("tool_calls"))

    @staticmethod
    def _serialize_transcript(
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Serialize canonical transcript data for LiteLLM's wire contract.

        LiteLLM expects historical assistant ``tool_calls[].function.arguments``
        to be JSON strings when it reconstructs a continuation request, including
        Ollama. Trusted-data policy and security observation remain in the agent
        loop, not this transport adapter.
        """
        normalized: list[dict[str, Any]] = []
        for message in messages:
            copy = dict(message)
            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, list):
                normalized_calls: list[dict[str, Any]] = []
                for raw_call in tool_calls:
                    if not isinstance(raw_call, dict):
                        normalized_calls.append(raw_call)
                        continue
                    call = dict(raw_call)
                    function = raw_call.get("function")
                    if isinstance(function, dict):
                        normalized_function = dict(function)
                        arguments = normalized_function.get("arguments")
                        if isinstance(arguments, dict):
                            normalized_function["arguments"] = json.dumps(
                                arguments, ensure_ascii=False, separators=(",", ":")
                            )
                        call["function"] = normalized_function
                    normalized_calls.append(call)
                copy["tool_calls"] = normalized_calls
            normalized.append(copy)
        return normalized

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
