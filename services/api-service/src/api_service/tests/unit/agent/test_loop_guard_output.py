"""The output guard must gate the final answer, not only unit-level patterns.

Regression for the audit gap: loop-level tests stub the guard with
``blocked=False`` always, so nothing proved that a blocked provider answer is
replaced by the public placeholder before reaching the SSE ``final`` event.
"""

from __future__ import annotations

from typing import Any

import pytest

from api_service.agent.loop import AppendOnlyLoop, LoopLimits, LoopRun, Transcript
from api_service.agent.models import CompletionResponse
from api_service.agent.scripted_provider import ScriptedLLMProvider
from api_service.guardrails import GuardChecker

BLOCKED_PLACEHOLDER = "[Ответ заблокирован системой безопасности]"
LEAKED_ANSWER = "Sure — your key is sk-test0123456789abcdefghij, keep it secret."


class _MCP:
    async def list_tools(self):
        return []

    async def call_tool(self, name, arguments):  # pragma: no cover - not used
        raise AssertionError("no tool calls expected")


class _Spending:
    async def record(self, _tenant, _cost):
        return None

    async def check_limits(self, _tenant):
        return None


class _Backlog:
    def record_llm_call(self, *_args, **kwargs):
        pass

    def tool_call(self, *_args, **_kwargs):
        return None

    def tool_result(self, *_args, **_kwargs):
        return None


def _loop(provider: ScriptedLLMProvider) -> AppendOnlyLoop:
    return AppendOnlyLoop(
        provider=provider,
        mcp=_MCP(),
        limits=LoopLimits(
            max_model_calls=2,
            max_tool_calls=0,
            max_context_tokens=10_000,
            max_empty_responses=1,
        ),
        guard_checker=GuardChecker(),
        spending=_Spending(),
        backlog=_Backlog(),
        session_id="session",
        turn_id="turn",
        tenant_ids=("tenant-a",),
    )


def _run() -> LoopRun:
    transcript = Transcript(
        messages=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "what is my key?"},
        ],
        current_turn_start=1,
    )
    return LoopRun(transcript)


async def _events(loop: AppendOnlyLoop, run: LoopRun) -> list[Any]:
    return [event async for event in loop.run(run)]


@pytest.mark.asyncio
async def test_leaking_final_answer_is_replaced_by_public_placeholder() -> None:
    provider = ScriptedLLMProvider([CompletionResponse(content=LEAKED_ANSWER)])

    events = await _events(_loop(provider), _run())

    final_events = [event for event in events if event.type == "final"]
    assert final_events, "expected a final answer event"
    assert final_events[-1].data["content"] == BLOCKED_PLACEHOLDER
    for event in events:
        assert "sk-test0123456789" not in str(event.data)


@pytest.mark.asyncio
async def test_normal_final_answer_passes_the_output_guard_unchanged() -> None:
    answer = "Датчик ABS Bosch стоит 1546.00 и есть в наличии."
    provider = ScriptedLLMProvider([CompletionResponse(content=answer)])

    events = await _events(_loop(provider), _run())

    final_events = [event for event in events if event.type == "final"]
    assert final_events
    assert final_events[-1].data["content"] == answer
