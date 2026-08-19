from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from litellm.types.utils import Choices, Message, ModelResponse, Usage

from api_service.agent.litellm_provider import LiteLLMProvider, ProviderProtocolError
from api_service.agent.models import CompletionRequest


def _response(*, content: str = "", tool_calls=None, cost: float | None = None):
    message = Message(content=content, role="assistant")
    message.tool_calls = tool_calls or []
    response = ModelResponse(
        id="response",
        created=1,
        model="openai/test",
        object="chat.completion",
        choices=[Choices(index=0, finish_reason="stop", message=message)],
    )
    usage = Usage(prompt_tokens=2, completion_tokens=3, total_tokens=5)
    usage.cost = cost
    response.usage = usage
    return response


@pytest.mark.asyncio
async def test_litellm_adapter_returns_only_native_structured_tool_calls() -> None:
    raw = type(
        "Call",
        (),
        {
            "id": "call-1",
            "function": type(
                "Function", (), {"name": "search", "arguments": '{"query":"Bosch"}'}
            )(),
        },
    )()
    provider = LiteLLMProvider("openai/test")
    with patch(
        "api_service.agent.litellm_provider.litellm.acompletion",
        new=AsyncMock(return_value=_response(tool_calls=[raw], cost=0.25)),
    ):
        response = await provider.complete(CompletionRequest(messages=[]))

    assert response.tool_calls[0].id == "call-1"
    assert response.tool_calls[0].name == "search"
    assert response.tool_calls[0].arguments == {"query": "Bosch"}
    assert response.cost == 0.25


@pytest.mark.asyncio
async def test_litellm_adapter_passes_raw_model_and_explicit_provider() -> None:
    """LiteLLM receives provider routing separately from the configured model ID."""
    completion = AsyncMock(return_value=_response(content="done"))
    provider = LiteLLMProvider("minimax-m3:cloud", provider="ollama")

    with patch("api_service.agent.litellm_provider.litellm.acompletion", completion):
        await provider.complete(CompletionRequest(messages=[]))

    assert completion.await_args.kwargs["model"] == "minimax-m3:cloud"
    assert completion.await_args.kwargs["custom_llm_provider"] == "ollama"


@pytest.mark.asyncio
async def test_litellm_adapter_serializes_transcript_tool_arguments() -> None:
    """LiteLLM receives wire-format arguments while the canonical transcript stays typed."""
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "search", "arguments": {"query": "Bosch"}},
                }
            ],
        }
    ]
    completion = AsyncMock(return_value=_response(content="done"))
    provider = LiteLLMProvider("ollama/test")

    with patch("api_service.agent.litellm_provider.litellm.acompletion", completion):
        await provider.complete(CompletionRequest(messages=messages))

    outgoing = completion.await_args.kwargs["messages"]
    assert outgoing[0]["tool_calls"][0]["function"]["arguments"] == '{"query":"Bosch"}'
    assert messages[0]["tool_calls"][0]["function"]["arguments"] == {"query": "Bosch"}


@pytest.mark.asyncio
async def test_litellm_adapter_rejects_malformed_native_arguments() -> None:
    raw = type(
        "Call",
        (),
        {
            "id": "call-1",
            "function": type(
                "Function", (), {"name": "search", "arguments": "not-json"}
            )(),
        },
    )()
    provider = LiteLLMProvider("openai/test")
    with patch(
        "api_service.agent.litellm_provider.litellm.acompletion",
        new=AsyncMock(return_value=_response(tool_calls=[raw])),
    ):
        with pytest.raises(ProviderProtocolError, match="invalid JSON"):
            await provider.complete(CompletionRequest(messages=[]))


@pytest.mark.asyncio
async def test_litellm_adapter_never_interprets_text_as_a_tool_call() -> None:
    provider = LiteLLMProvider("openai/test")
    text = '{"name":"search","arguments":{"query":"Bosch"}}'
    with patch(
        "api_service.agent.litellm_provider.litellm.acompletion",
        new=AsyncMock(return_value=_response(content=text)),
    ):
        response = await provider.complete(CompletionRequest(messages=[]))

    assert response.content == text
    assert response.tool_calls == []
