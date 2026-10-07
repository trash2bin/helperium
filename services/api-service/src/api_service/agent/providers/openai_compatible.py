"""Direct OpenAI-compatible transport that bypasses LiteLLM.

This is the lightweight counterpart to ``LiteLLMProvider``: it talks to a
``/v1/chat/completions`` endpoint directly with httpx and parses the OpenAI
wire format itself.  It implements the same typed provider protocol so the
agent loop, factory and scripted-fixture path cannot tell the difference.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import httpx

from helperium_sdk.settings import settings
from api_service.prometheus_metrics import (
    llm_completion_attempts_total,
    llm_retries_total,
    llm_retry_delay_seconds,
    llm_retry_exhausted_total,
    llm_retry_suppressed_total,
)

from ..completion_retry import (
    CompletionRetryExecutor,
    CompletionRetryPolicy,
    ThrottledHTTPError,
    TransientHTTPError,
)
from ..models import CompletionRequest, CompletionResponse, ToolCall, UsageInfo
from .base import BaseLLMProvider


logger = logging.getLogger("api_service.agent.providers.openai_compatible")

# Pool sizing for the shared client. Deliberately generous relative to one
# worker's share of concurrent turns: a too-small pool queues requests instead
# of opening sockets, which shows up as latency rather than as an error.
_MAX_CONNECTIONS = int(os.environ.get("LLM_HTTP_MAX_CONNECTIONS", "100"))
_MAX_KEEPALIVE_CONNECTIONS = int(
    os.environ.get("LLM_HTTP_MAX_KEEPALIVE_CONNECTIONS", "20")
)
_KEEPALIVE_EXPIRY_S = float(os.environ.get("LLM_HTTP_KEEPALIVE_EXPIRY_S", "30.0"))

SHARED_HTTP_LIMITS = httpx.Limits(
    max_connections=_MAX_CONNECTIONS,
    max_keepalive_connections=_MAX_KEEPALIVE_CONNECTIONS,
    keepalive_expiry=_KEEPALIVE_EXPIRY_S,
)

_shared_http_client: httpx.AsyncClient | None = None
_shared_http_client_loop: asyncio.AbstractEventLoop | None = None


async def get_shared_http_client() -> httpx.AsyncClient:
    """Return one pooled client for the running event loop.

    The pool must outlive the provider: ``factory.resolve_llm`` builds a new
    provider on every turn, so a client owned by the instance would still be
    rebuilt per turn. Keying on the event loop keeps one pool per uvicorn
    worker process and keeps the pool off a closed loop after a restart.
    """
    global _shared_http_client, _shared_http_client_loop

    loop = asyncio.get_running_loop()
    stale: httpx.AsyncClient | None = None
    if _shared_http_client is not None and _shared_http_client_loop is not loop:
        # Different loop (uvicorn worker restart, test teardown): the old loop
        # is gone, so its connections are not reusable here.
        stale, _shared_http_client = _shared_http_client, None
        _shared_http_client_loop = None
    if _shared_http_client is None:
        _shared_http_client = httpx.AsyncClient(limits=SHARED_HTTP_LIMITS)
        _shared_http_client_loop = loop
    if stale is not None:
        await _close_quietly(stale)
    return _shared_http_client


async def aclose_shared_http_client() -> None:
    """Release the pooled client (application shutdown and test isolation)."""
    global _shared_http_client, _shared_http_client_loop

    client, _shared_http_client = _shared_http_client, None
    _shared_http_client_loop = None
    if client is not None:
        await _close_quietly(client)


async def _close_quietly(client: httpx.AsyncClient) -> None:
    try:
        await client.aclose()
    except Exception:  # noqa: BLE001 — teardown must never break a completion
        # The owning loop may already be closed: its sockets went with it and
        # httpx cannot be asked to release them from a foreign loop.
        logger.debug("shared httpx client close skipped", exc_info=True)


class ProviderProtocolError(ValueError):
    """The direct provider returned a response that cannot be represented."""


class OpenAICompatibleProvider(BaseLLMProvider):
    """Minimal typed adapter around a raw OpenAI ``/v1/chat/completions``."""

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
        self._reuse_http_client = settings.llm_http_client_reuse
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
        endpoint = self._endpoint(request)
        messages = self._serialize_transcript(request.messages)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if request.tools:
            payload["tools"] = request.tools
        if self.enable_thinking:
            payload["think"] = True
        headers = self._headers()

        provider_label = self.provider or "inferred"

        async def completion_attempt(attempt_timeout: float) -> httpx.Response:
            if self._reuse_http_client:
                # The pool outlives the attempt, so the deadline rides on the
                # request: one slow provider cannot age out the shared pool.
                client = await get_shared_http_client()
                response = await client.post(
                    endpoint, json=payload, headers=headers, timeout=attempt_timeout
                )
            else:
                # Rollback path (LLM_HTTP_CLIENT_REUSE=0): original lifecycle.
                async with httpx.AsyncClient(timeout=attempt_timeout) as client:
                    response = await client.post(
                        endpoint, json=payload, headers=headers
                    )
            if response.status_code >= 400:
                self._raise_for_status(response)
            return response

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
        if response.status_code >= 400:
            self._raise_for_status(response)
        try:
            body = response.json()
        except json.JSONDecodeError as exc:
            raise ProviderProtocolError(
                f"direct provider returned non-JSON response: {exc}"
            ) from exc
        return self._parse(body)

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        """Map an HTTP error onto the retry executor's approved categories.

        A failed attempt must reach ``retry_category`` as a *known* failure so
        the bounded retry policy can decide, not as an unknown error that is
        suppressed immediately.
        """
        status = response.status_code
        raw_retry_after = response.headers.get("Retry-After") or response.headers.get(
            "retry-after"
        )
        try:
            retry_after = float(raw_retry_after) if raw_retry_after else None
        except (TypeError, ValueError):
            retry_after = None
        body = response.text[:200]
        if status == 429:
            raise ThrottledHTTPError(status, body, retry_after)
        if status in (408, 500, 502, 503, 504):
            raise TransientHTTPError(status, body, retry_after)
        raise ProviderProtocolError(f"direct provider returned HTTP {status}: {body}")

    def _endpoint(self, request: CompletionRequest) -> str:
        base = (self.api_base or "").rstrip("/")
        if not base:
            base = "http://127.0.0.1:11434"
        if base.endswith("/v1/chat/completions"):
            return base
        if not base.endswith("/v1"):
            base = f"{base}/v1"
        return f"{base}/chat/completions"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _parse(self, body: dict[str, Any]) -> CompletionResponse:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderProtocolError("direct provider returned no choices")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            raise ProviderProtocolError("direct provider returned no message")
        content = message.get("content") or ""
        tool_calls = [self._tool_call(raw) for raw in (message.get("tool_calls") or [])]
        usage_raw = body.get("usage")
        return CompletionResponse(
            content=content,
            tool_calls=tool_calls,
            usage=self._usage(usage_raw),
            cost=0.0,
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

    def _tool_call(self, raw: Any) -> ToolCall:
        if not isinstance(raw, dict):
            raise ProviderProtocolError("tool call must be an object")
        function = raw.get("function")
        call_id = raw.get("id")
        name = function.get("name") if isinstance(function, dict) else None
        raw_arguments = (
            function.get("arguments") if isinstance(function, dict) else None
        )
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
    def _serialize_transcript(
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
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
    def _usage(raw: Any) -> UsageInfo | None:
        if not isinstance(raw, dict):
            return None
        return UsageInfo(
            prompt_tokens=raw.get("prompt_tokens", 0) or 0,
            completion_tokens=raw.get("completion_tokens", 0) or 0,
            total_tokens=raw.get("total_tokens", 0) or 0,
        )
