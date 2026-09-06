"""Unit tests for the response-shape middleware (AnswerNormalizer)."""

from __future__ import annotations

import pytest

from api_service.agent.answer_normalizer import (
    AnswerNormalizer,
    is_tool_call_markup,
    unwrap_answer_envelope,
)
from api_service.agent.models import CompletionRequest, CompletionResponse, ToolCall
from api_service.agent.scripted_provider import ScriptedLLMProvider


def _request() -> CompletionRequest:
    return CompletionRequest(messages=[{"role": "user", "content": "hi"}], tools=[])


@pytest.mark.asyncio
async def test_fabricated_tool_call_envelope_becomes_an_empty_round() -> None:
    markup = (
        'Tool Calls: [{"id": "call_1", "type": "function", '
        '"function": {"name": "search", "arguments": {"query": "pads"}}}]'
    )
    provider = AnswerNormalizer(
        ScriptedLLMProvider([CompletionResponse(content=markup)])
    )

    response = await provider.complete(_request())

    assert response.content == ""
    assert response.tool_calls == []


@pytest.mark.asyncio
async def test_single_key_answer_envelope_is_unwrapped() -> None:
    provider = AnswerNormalizer(
        ScriptedLLMProvider(
            [
                CompletionResponse(
                    content='{"answer": "\\u0410\\u0440\\u0442\\u0438\\u043a\\u0443\\u043b \\u043d\\u0435 \\u043d\\u0430\\u0439\\u0434\\u0435\\u043d."}'
                )
            ]
        )
    )

    response = await provider.complete(_request())

    assert response.content == "Артикул не найден."


@pytest.mark.asyncio
async def test_native_tool_calls_pass_through_untouched() -> None:
    calls = [
        ToolCall(id="call-1", name="search", arguments={"query": "pads"}),
    ]
    provider = AnswerNormalizer(
        ScriptedLLMProvider(
            [CompletionResponse(content='{"answer": "ignored"}', tool_calls=calls)]
        )
    )

    response = await provider.complete(_request())

    assert response.content == '{"answer": "ignored"}'
    assert response.tool_calls == calls


@pytest.mark.asyncio
async def test_data_shaped_json_and_plain_text_pass_through() -> None:
    cases = [
        '[{"id": 1, "name": "Brake pads"}]',
        '{"answer": "x", "confidence": 0.9}',
        '{"preview": [{"id": 1}], "total": 1}',
        "Просто текстовый ответ.",
        "",
    ]
    for content in cases:
        provider = AnswerNormalizer(
            ScriptedLLMProvider([CompletionResponse(content=content)])
        )
        response = await provider.complete(_request())
        assert response.content == content, content


def test_is_tool_call_markup_shapes() -> None:
    assert is_tool_call_markup(
        'Tool Calls: [{"function": {"name": "get", "arguments": {}}}]'
    )
    assert is_tool_call_markup('```json\n{"name": "get", "arguments": {}}\n```')
    assert not is_tool_call_markup('[{"id": 1, "name": "pads"}]')
    assert not is_tool_call_markup("обычный ответ")
    assert not is_tool_call_markup('Tool Calls: [{"name": 1}]')


def test_unwrap_answer_envelope_cases() -> None:
    assert unwrap_answer_envelope('{"answer": "найдено"}') == "найдено"
    assert unwrap_answer_envelope('```json\n{"text": "готово"}\n```') == "готово"
    assert unwrap_answer_envelope('{"answer": {"text": "вложено"}}') == "вложено"
    assert unwrap_answer_envelope('{"other": "x"}') is None
    assert unwrap_answer_envelope('{"answer": ""}') is None
    assert unwrap_answer_envelope('{"answer": 42}') is None
    assert unwrap_answer_envelope("не json") is None
    assert unwrap_answer_envelope('[{"answer": "x"}]') is None
