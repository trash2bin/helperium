"""Regression tests for ordered LLM execution fallback."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from api_service.agent.models import CompletionRequest, CompletionResponse
from api_service.agent.provider_pool import FallbackProvider


class StubProvider:
    """Deterministic provider that can fail before returning a response."""

    def __init__(
        self,
        model: str,
        calls: list[str],
        failures: Sequence[Exception] = (),
    ) -> None:
        self.model = model
        self._calls = calls
        self._failures = list(failures)

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        del request
        self._calls.append(self.model)
        if self._failures:
            raise self._failures.pop(0)
        return CompletionResponse(content=f"answer from {self.model}")


def _request() -> CompletionRequest:
    return CompletionRequest(messages=[{"role": "user", "content": "test"}])


@pytest.mark.asyncio
async def test_tries_priority_candidates_in_order_after_a_runtime_failure():
    calls: list[str] = []
    primary = StubProvider("openai/primary", calls, [TimeoutError("down")])
    fallback = StubProvider("ollama/fallback", calls)
    provider = FallbackProvider([primary, fallback])

    result = await provider.complete(_request())

    assert result.content == "answer from ollama/fallback"
    assert calls == ["openai/primary", "ollama/fallback"]
    assert provider.model == "ollama/fallback"


@pytest.mark.asyncio
async def test_keeps_the_successful_provider_for_the_remainder_of_a_turn():
    calls: list[str] = []
    primary = StubProvider("openai/primary", calls, [TimeoutError("down")])
    fallback = StubProvider("ollama/fallback", calls)
    provider = FallbackProvider([primary, fallback])

    await provider.complete(_request())
    result = await provider.complete(_request())

    assert result.content == "answer from ollama/fallback"
    assert calls == ["openai/primary", "ollama/fallback", "ollama/fallback"]
    assert provider.model == "ollama/fallback"


@pytest.mark.asyncio
async def test_raises_only_after_every_candidate_failed():
    calls: list[str] = []
    provider = FallbackProvider(
        [
            StubProvider("openai/primary", calls, [TimeoutError("down")]),
            StubProvider("ollama/fallback", calls, [ConnectionError("down")]),
        ]
    )

    with pytest.raises(RuntimeError, match="All 2 LLM providers failed"):
        await provider.complete(_request())

    assert calls == ["openai/primary", "ollama/fallback"]
