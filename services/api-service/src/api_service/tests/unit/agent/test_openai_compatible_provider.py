"""Direct OpenAI-compatible provider: contract, parsing and retry categories."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from api_service.agent.completion_retry import (
    CompletionRetryExecutor,
    CompletionRetryPolicy,
    ThrottledHTTPError,
    TransientHTTPError,
    retry_category,
)
from api_service.agent.models import CompletionRequest
from api_service.agent.providers.openai_compatible import (
    OpenAICompatibleProvider,
    ProviderProtocolError,
)


def _completion_response(**overrides) -> dict:
    body = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": overrides.get("content", "done"),
                    "tool_calls": overrides.get("tool_calls", []),
                }
            }
        ],
        "usage": overrides.get(
            "usage", {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}
        ),
    }
    if "finish_reason" in overrides:
        body["choices"][0]["finish_reason"] = overrides["finish_reason"]
    return body


def _http_response(
    status_code: int, body: dict | str | None = None, headers: dict | None = None
) -> httpx.Response:
    if isinstance(body, dict):
        content = json.dumps(body).encode()
    elif isinstance(body, str):
        content = body.encode()
    else:
        content = b""
    return httpx.Response(
        status_code=status_code,
        content=content,
        headers=headers or {},
        request=httpx.Request("POST", "http://x/v1/chat/completions"),
    )


@pytest.mark.asyncio
async def test_direct_provider_parses_native_tool_calls() -> None:
    response = _http_response(
        200,
        _completion_response(
            content="",
            tool_calls=[
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "search", "arguments": '{"query":"Bosch"}'},
                }
            ],
        ),
    )
    provider = OpenAICompatibleProvider("openai/test", api_base="http://llm:8000")
    with patch(
        "api_service.agent.providers.openai_compatible.httpx.AsyncClient.post",
        new=AsyncMock(return_value=response),
    ) as post:
        result = await provider.complete(
            CompletionRequest(messages=[{"role": "user", "content": "hi"}])
        )

    assert post.await_args.args[0] == "http://llm:8000/v1/chat/completions"
    payload = post.await_args.kwargs["json"]
    assert payload["model"] == "openai/test"
    assert payload["messages"][0]["content"] == "hi"
    assert result.tool_calls[0].id == "call-1"
    assert result.tool_calls[0].name == "search"
    assert result.tool_calls[0].arguments == {"query": "Bosch"}
    assert result.content == ""
    assert result.usage is not None and result.usage.total_tokens == 5


@pytest.mark.asyncio
async def test_direct_provider_sends_tools_and_authorization() -> None:
    response = _http_response(200, _completion_response(content="ok"))
    provider = OpenAICompatibleProvider(
        "m", api_key="sk-test", api_base="http://llm:8000"
    )
    tools = [{"type": "function", "function": {"name": "search"}}]
    with patch(
        "api_service.agent.providers.openai_compatible.httpx.AsyncClient.post",
        new=AsyncMock(return_value=response),
    ) as post:
        await provider.complete(CompletionRequest(messages=[], tools=tools))

    assert post.await_args.kwargs["json"]["tools"] == tools
    assert post.await_args.kwargs["headers"]["Authorization"] == "Bearer sk-test"


@pytest.mark.asyncio
async def test_direct_provider_retries_throttled_then_succeeds() -> None:
    """429 must be retried under the shared bounded retry policy, not suppressed."""
    throttled = _http_response(429, "slow down", headers={"Retry-After": "2"})
    ok = _http_response(200, _completion_response(content="after retry"))
    provider = OpenAICompatibleProvider("m", api_base="http://llm:8000")
    provider._retry_executor = CompletionRetryExecutor(
        CompletionRetryPolicy(
            max_attempts=3,
            max_elapsed_seconds=30.0,
            transient_base_seconds=0.0,
            throttled_base_seconds=0.0,
            max_backoff_seconds=0.0,
        )
    )
    with patch(
        "api_service.agent.providers.openai_compatible.httpx.AsyncClient.post",
        new=AsyncMock(side_effect=[throttled, ok]),
    ) as post:
        result = await provider.complete(CompletionRequest(messages=[]))

    assert post.await_count == 2
    assert result.content == "after retry"


@pytest.mark.asyncio
async def test_direct_provider_retries_transient_5xx() -> None:
    transient = _http_response(503, "unavailable")
    ok = _http_response(200, _completion_response(content="ok"))
    provider = OpenAICompatibleProvider("m", api_base="http://llm:8000")
    provider._retry_executor = CompletionRetryExecutor(
        CompletionRetryPolicy(
            max_attempts=2,
            max_elapsed_seconds=30.0,
            transient_base_seconds=0.0,
            throttled_base_seconds=0.0,
            max_backoff_seconds=0.0,
        )
    )
    with patch(
        "api_service.agent.providers.openai_compatible.httpx.AsyncClient.post",
        new=AsyncMock(side_effect=[transient, ok]),
    ) as post:
        result = await provider.complete(CompletionRequest(messages=[]))

    assert post.await_count == 2
    assert result.content == "ok"


@pytest.mark.asyncio
async def test_direct_provider_does_not_retry_client_error() -> None:
    provider = OpenAICompatibleProvider("m", api_base="http://llm:8000")
    provider._retry_executor = CompletionRetryExecutor(
        CompletionRetryPolicy(
            max_attempts=3,
            max_elapsed_seconds=30.0,
            transient_base_seconds=0.0,
            throttled_base_seconds=0.0,
            max_backoff_seconds=0.0,
        )
    )
    bad = _http_response(400, "bad request")
    with patch(
        "api_service.agent.providers.openai_compatible.httpx.AsyncClient.post",
        new=AsyncMock(return_value=bad),
    ) as post:
        with pytest.raises(ProviderProtocolError):
            await provider.complete(CompletionRequest(messages=[]))

    assert post.await_count == 1


@pytest.mark.asyncio
async def test_direct_provider_transport_error_is_retried() -> None:
    ok = _http_response(200, _completion_response(content="ok"))
    provider = OpenAICompatibleProvider("m", api_base="http://llm:8000")
    provider._retry_executor = CompletionRetryExecutor(
        CompletionRetryPolicy(
            max_attempts=2,
            max_elapsed_seconds=30.0,
            transient_base_seconds=0.0,
            throttled_base_seconds=0.0,
            max_backoff_seconds=0.0,
        )
    )
    connect_error = httpx.ConnectError("refused")
    with patch(
        "api_service.agent.providers.openai_compatible.httpx.AsyncClient.post",
        new=AsyncMock(side_effect=[connect_error, ok]),
    ) as post:
        result = await provider.complete(CompletionRequest(messages=[]))

    assert post.await_count == 2
    assert result.content == "ok"


def test_retry_category_recognises_direct_provider_errors() -> None:
    assert retry_category(ThrottledHTTPError(429, "slow")) == "throttled"
    assert retry_category(TransientHTTPError(503, "down")) == "transient"
    assert retry_category(httpx.ConnectError("refused")) == "transient"
    assert retry_category(httpx.ReadTimeout("slow")) == "transient"
    assert retry_category(ValueError("unexpected")) is None
    assert retry_category(ProviderProtocolError("bad body")) is None
