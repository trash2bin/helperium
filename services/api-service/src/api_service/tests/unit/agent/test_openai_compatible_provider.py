"""Direct OpenAI-compatible provider: contract, parsing and retry categories."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from helperium_sdk.settings import settings
from api_service.agent.completion_retry import (
    CompletionRetryExecutor,
    CompletionRetryPolicy,
    ThrottledHTTPError,
    TransientHTTPError,
    retry_category,
)
from api_service.agent.models import CompletionRequest
from api_service.agent.providers import openai_compatible
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


# ── HTTP client lifecycle ─────────────────────────────────────────────────────
# The direct transport used to build a fresh httpx.AsyncClient for every attempt
# and close it as soon as the response came back. Providers are resolved per
# turn, so at 61.7 rps with ~2.5 LLM calls per turn that is ~9200 short-lived
# sockets per minute -- measured on the Linux stress stand as 9188 TIME_WAIT on
# the stub port, matching the formula exactly. These tests hold the reuse
# contract that removes that churn.


def _spy_on_client_pool(
    monkeypatch,
) -> tuple[list[dict[str, Any]], AsyncMock]:
    """Replace ``httpx.AsyncClient`` with a recorder that opens no sockets.

    The recorded ``post`` mock is shared by every instance on purpose: the
    contract is about how many connection pools exist for a number of requests.
    """
    constructions: list[dict[str, Any]] = []
    post = AsyncMock(return_value=_http_response(200, _completion_response()))

    class _Spy:
        def __init__(self, **kwargs: Any) -> None:
            constructions.append(kwargs)
            self.aclose = AsyncMock()

        async def __aenter__(self) -> "_Spy":
            return self

        async def __aexit__(self, *exc: Any) -> None:
            await self.aclose()

    # Bound after the class body: a class scope cannot close over `post`.
    _Spy.post = post  # type: ignore[attr-defined]

    monkeypatch.setattr(openai_compatible.httpx, "AsyncClient", _Spy)
    return constructions, post


async def _release_shared_client() -> None:
    """Free whatever the provider cached, so state cannot leak between tests."""
    close = getattr(openai_compatible, "aclose_shared_http_client", None)
    if close is not None:
        await close()


def _retrying_executor() -> CompletionRetryExecutor:
    return CompletionRetryExecutor(
        CompletionRetryPolicy(
            max_attempts=3,
            max_elapsed_seconds=30.0,
            transient_base_seconds=0.0,
            throttled_base_seconds=0.0,
            max_backoff_seconds=0.0,
        )
    )


@pytest.mark.asyncio
async def test_direct_provider_reuses_one_http_client_across_attempts_and_turns(
    monkeypatch,
) -> None:
    """A pooled client is built once, not once per attempt or per turn."""
    constructions, post = _spy_on_client_pool(monkeypatch)
    monkeypatch.setattr(settings, "llm_http_client_reuse", True)

    try:
        provider = OpenAICompatibleProvider("m", api_base="http://llm:8000")
        provider._retry_executor = _retrying_executor()
        # First turn needs two attempts, second turn needs one: three requests
        # must still share one connection pool.
        post.side_effect = [
            _http_response(429, "slow down"),
            _http_response(200, _completion_response()),
            _http_response(200, _completion_response()),
        ]
        await provider.complete(CompletionRequest(messages=[]))
        await provider.complete(CompletionRequest(messages=[]))

        assert post.await_count == 3
        assert len(constructions) == 1, (
            "the direct transport built a new httpx.AsyncClient per attempt: "
            f"{len(constructions)} clients for 3 requests"
        )
    finally:
        await _release_shared_client()


@pytest.mark.asyncio
async def test_direct_provider_clamps_each_request_by_the_attempt_timeout(
    monkeypatch,
) -> None:
    """The retry deadline is per attempt, so the timeout must ride on the request."""
    constructions, post = _spy_on_client_pool(monkeypatch)
    monkeypatch.setattr(settings, "llm_http_client_reuse", True)

    try:
        provider = OpenAICompatibleProvider(
            "m", api_base="http://llm:8000", timeout=30.0
        )
        provider._retry_executor = CompletionRetryExecutor(
            CompletionRetryPolicy(
                max_attempts=1,
                max_elapsed_seconds=30.0,
                transient_base_seconds=0.0,
                throttled_base_seconds=0.0,
                max_backoff_seconds=0.0,
            )
        )
        await provider.complete(CompletionRequest(messages=[]))

        request_timeout = post.await_args.kwargs.get("timeout")
        assert request_timeout is not None, (
            "a pooled client cannot carry one deadline for all requests: "
            "the per-attempt timeout must be passed on the request"
        )
        assert request_timeout == pytest.approx(30.0)
        # The pool itself must not carry the per-attempt deadline.
        assert "timeout" not in constructions[0]
    finally:
        await _release_shared_client()


@pytest.mark.asyncio
async def test_direct_provider_bounds_the_shared_connection_pool(monkeypatch) -> None:
    """An unbounded pool would reintroduce the socket churn under a retry storm."""
    constructions, _post = _spy_on_client_pool(monkeypatch)
    monkeypatch.setattr(settings, "llm_http_client_reuse", True)

    try:
        provider = OpenAICompatibleProvider("m", api_base="http://llm:8000")
        await provider.complete(CompletionRequest(messages=[]))

        limits = constructions[0].get("limits")
        assert isinstance(limits, httpx.Limits)
        assert isinstance(limits.max_connections, int)
        assert 0 < limits.max_connections <= 512
        assert isinstance(limits.max_keepalive_connections, int)
        assert 0 <= limits.max_keepalive_connections <= limits.max_connections
    finally:
        await _release_shared_client()


@pytest.mark.asyncio
async def test_direct_provider_releases_the_shared_client_on_request(
    monkeypatch,
) -> None:
    """Shutdown and test isolation need an explicit way to drop the pool."""
    constructions, _post = _spy_on_client_pool(monkeypatch)
    monkeypatch.setattr(settings, "llm_http_client_reuse", True)

    try:
        provider = OpenAICompatibleProvider("m", api_base="http://llm:8000")
        await provider.complete(CompletionRequest(messages=[]))
        await provider.complete(CompletionRequest(messages=[]))
        assert len(constructions) == 1

        await _release_shared_client()
        await provider.complete(CompletionRequest(messages=[]))
        assert len(constructions) == 2, (
            "the pooled client must be rebuilt after the release hook runs"
        )
    finally:
        await _release_shared_client()


@pytest.mark.asyncio
async def test_direct_provider_can_fall_back_to_per_attempt_clients(
    monkeypatch,
) -> None:
    """`LLM_HTTP_CLIENT_REUSE=0` keeps the old lifecycle as a rollback switch."""
    constructions, _post = _spy_on_client_pool(monkeypatch)
    monkeypatch.setattr(settings, "llm_http_client_reuse", False)

    try:
        provider = OpenAICompatibleProvider("m", api_base="http://llm:8000")
        await provider.complete(CompletionRequest(messages=[]))

        assert len(constructions) == 1
        # The rollback path keeps the deadline on the client itself.
        assert constructions[0].get("timeout") is not None
    finally:
        await _release_shared_client()
