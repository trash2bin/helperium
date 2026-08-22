from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import litellm
import pytest
from litellm.types.utils import Choices, Message, ModelResponse, Usage

from api_service.agent.completion_retry import (
    CompletionRetryExecutor,
    CompletionRetryPolicy,
    retry_after_seconds,
    retry_category,
)
from api_service.agent.litellm_provider import LiteLLMProvider
from api_service.agent.models import CompletionRequest, CompletionResponse
from api_service.agent.provider_pool import FallbackProvider


def _response(content: str = "done") -> ModelResponse:
    response = ModelResponse(
        id="response",
        created=1,
        model="openai/test",
        object="chat.completion",
        choices=[
            Choices(
                index=0,
                finish_reason="stop",
                message=Message(content=content, role="assistant"),
            )
        ],
    )
    response.usage = Usage(prompt_tokens=2, completion_tokens=3, total_tokens=5)
    return response


def _executor(
    *,
    max_attempts: int = 3,
    max_elapsed_seconds: float = 20.0,
    monotonic: Callable[[], float] | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> CompletionRetryExecutor:
    return CompletionRetryExecutor(
        CompletionRetryPolicy(
            max_attempts=max_attempts,
            max_elapsed_seconds=max_elapsed_seconds,
            transient_base_seconds=0.25,
            throttled_base_seconds=1.0,
            max_backoff_seconds=4.0,
        ),
        monotonic=monotonic or __import__("time").monotonic,
        sleep=sleep or AsyncMock(),
        uniform=lambda _low, _high: 0.0,
    )


def _rate_limited(*, retry_after: str | None = None) -> litellm.RateLimitError:
    response = None
    if retry_after is not None:
        response = httpx.Response(429, headers={"Retry-After": retry_after})
    return litellm.RateLimitError(
        "busy",
        llm_provider="test",
        model="openai/test",
        response=response,
    )


def test_retry_category_is_allowlist_only() -> None:
    assert retry_category(_rate_limited()) == "throttled"
    assert (
        retry_category(
            litellm.Timeout("too slow", model="openai/test", llm_provider="test")
        )
        == "transient"
    )
    assert (
        retry_category(litellm.APIError(503, "unavailable", "test", "openai/test"))
        == "transient"
    )
    assert (
        retry_category(litellm.APIError(400, "invalid", "test", "openai/test")) is None
    )
    assert retry_category(ValueError("unknown")) is None


def test_retry_after_parses_seconds_and_rejects_invalid_values() -> None:
    assert retry_after_seconds(_rate_limited(retry_after="7")) == 7.0
    assert retry_after_seconds(_rate_limited(retry_after="not-a-delay")) is None

    lower_case = Exception("busy")
    lower_case.response = type("Response", (), {"headers": {"retry-after": "3"}})()
    assert retry_after_seconds(lower_case) == 3.0


@pytest.mark.asyncio
async def test_litellm_provider_retries_same_serialized_request_then_succeeds() -> None:
    sleep = AsyncMock()
    completion = AsyncMock(side_effect=[_rate_limited(), _response()])
    provider = LiteLLMProvider(
        "openai/test",
        provider="test",
        timeout=30.0,
        retry_executor=_executor(sleep=sleep),
    )
    request = CompletionRequest(
        messages=[{"role": "user", "content": "find product"}],
        tools=[{"type": "function", "function": {"name": "db_search"}}],
    )

    with patch("api_service.agent.litellm_provider.litellm.acompletion", completion):
        result = await provider.complete(request)

    assert result.content == "done"
    assert completion.await_count == 2
    first, second = (call.kwargs for call in completion.await_args_list)
    assert first["messages"] == second["messages"] == request.messages
    assert first["tools"] == second["tools"] == request.tools
    assert first["model"] == second["model"] == "openai/test"
    assert first["custom_llm_provider"] == second["custom_llm_provider"] == "test"
    assert first["timeout"] <= 20.0
    assert second["timeout"] <= first["timeout"]
    sleep.assert_awaited_once_with(0.0)


@pytest.mark.asyncio
async def test_non_retryable_provider_error_is_not_repeated() -> None:
    sleep = AsyncMock()
    completion = AsyncMock(
        side_effect=litellm.BadRequestError(
            "unsupported parameter",
            model="openai/test",
            llm_provider="test",
        )
    )
    provider = LiteLLMProvider(
        "openai/test",
        provider="test",
        retry_executor=_executor(sleep=sleep),
    )

    with (
        patch("api_service.agent.litellm_provider.litellm.acompletion", completion),
        pytest.raises(litellm.BadRequestError),
    ):
        await provider.complete(CompletionRequest(messages=[]))

    assert completion.await_count == 1
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancellation_is_reraised_without_retry_or_fallback() -> None:
    sleep = AsyncMock()
    completion = AsyncMock(side_effect=asyncio.CancelledError())
    provider = LiteLLMProvider(
        "openai/test",
        provider="test",
        retry_executor=_executor(sleep=sleep),
    )

    with (
        patch("api_service.agent.litellm_provider.litellm.acompletion", completion),
        pytest.raises(asyncio.CancelledError),
    ):
        await provider.complete(CompletionRequest(messages=[]))

    assert completion.await_count == 1
    sleep.assert_not_awaited()


@dataclass
class _Clock:
    value: float = 0.0

    def monotonic(self) -> float:
        return self.value

    async def sleep_past_deadline(self, _delay: float) -> None:
        self.value = 21.0


@pytest.mark.asyncio
async def test_deadline_prevents_a_second_physical_attempt() -> None:
    clock = _Clock()
    executor = _executor(monotonic=clock.monotonic, sleep=clock.sleep_past_deadline)
    attempts = 0
    exhausted: list[tuple[str, str]] = []

    failure = _rate_limited()

    async def call(_timeout: float) -> None:
        nonlocal attempts
        attempts += 1
        raise failure

    with pytest.raises(litellm.RateLimitError) as raised:
        await executor.run(
            call,
            provider_timeout=120.0,
            model="openai/test",
            provider="test",
            on_attempt=lambda: None,
            on_retry=lambda _category, _delay: None,
            on_exhausted=lambda category, reason: exhausted.append((category, reason)),
            on_suppressed=lambda _reason: None,
        )

    assert raised.value is failure
    assert attempts == 1
    assert exhausted == [("throttled", "deadline")]


@pytest.mark.asyncio
async def test_primary_retries_exhaust_before_fallback_provider_runs() -> None:
    primary_completion = AsyncMock(
        side_effect=[_rate_limited(), _rate_limited(), _rate_limited()]
    )
    primary = LiteLLMProvider(
        "primary/model",
        provider="test",
        retry_executor=_executor(),
    )
    backup = MagicMock()
    backup.model = "backup/model"
    backup.complete = AsyncMock(return_value=CompletionResponse(content="fallback"))
    providers = FallbackProvider([primary, backup])

    with patch(
        "api_service.agent.litellm_provider.litellm.acompletion", primary_completion
    ):
        response = await providers.complete(CompletionRequest(messages=[]))

    assert response.content == "fallback"
    assert primary_completion.await_count == 3
    backup.complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_retry_after_overrides_jitter_backoff() -> None:
    sleep = AsyncMock()
    executor = _executor(sleep=sleep)
    outcomes = iter([_rate_limited(retry_after="7"), "success"])

    async def call(_timeout: float) -> str:
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    result = await executor.run(
        call,
        provider_timeout=120.0,
        model="openai/test",
        provider="test",
        on_attempt=lambda: None,
        on_retry=lambda _category, _delay: None,
        on_exhausted=lambda _category, _reason: None,
        on_suppressed=lambda _reason: None,
    )

    assert result == "success"
    sleep.assert_awaited_once_with(7.0)


@pytest.mark.asyncio
async def test_cancellation_during_backoff_stops_before_second_attempt() -> None:
    calls = 0

    async def cancelled_sleep(_delay: float) -> None:
        raise asyncio.CancelledError()

    executor = _executor(sleep=cancelled_sleep)

    async def call(_timeout: float) -> None:
        nonlocal calls
        calls += 1
        raise _rate_limited()

    with pytest.raises(asyncio.CancelledError):
        await executor.run(
            call,
            provider_timeout=120.0,
            model="openai/test",
            provider="test",
            on_attempt=lambda: None,
            on_retry=lambda _category, _delay: None,
            on_exhausted=lambda _category, _reason: None,
            on_suppressed=lambda _reason: None,
        )

    assert calls == 1
