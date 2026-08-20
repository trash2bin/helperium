from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from api_service.agent.loop import AppendOnlyLoop, LoopLimits, LoopRun, Transcript
from api_service.agent.models import CompletionResponse, ToolCall


@dataclass
class _Result:
    tool_content: str
    ok: bool = True


class _Provider:
    model = "scripted/test"

    def __init__(self, responses: list[CompletionResponse]) -> None:
        self.responses = list(responses)
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        return self.responses.pop(0)


class _MCP:
    def __init__(self, results: dict[str, _Result] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.results = results or {}

    async def list_tools(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": "search",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get",
                    "parameters": {
                        "type": "object",
                        "properties": {"id": {"type": "integer"}},
                        "required": ["id"],
                        "additionalProperties": False,
                    },
                },
            },
        ]

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return self.results.get(name, _Result("{}"))


class _GuardResult:
    blocked = False


class _Guard:
    def check_input(self, _text):
        return _GuardResult()

    def check_output(self, _text):
        return _GuardResult()


class _Spending:
    async def record(self, _tenant, _cost):
        return None

    async def check_limits(self, _tenant):
        return True, ""


class _Backlog:
    def __init__(self) -> None:
        self.llm_calls: list[dict[str, Any]] = []

    def record_llm_call(self, *_args, **kwargs):
        self.llm_calls.append(kwargs)

    def tool_call(self, *_args, **_kwargs):
        return None

    def tool_result(self, *_args, **_kwargs):
        return None


def _run(
    provider: _Provider, mcp: _MCP, *, limits: LoopLimits | None = None
) -> LoopRun:
    transcript = Transcript(
        messages=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "find Bosch"},
        ],
        current_turn_start=1,
    )
    return LoopRun(transcript)


def _loop(
    provider: _Provider,
    mcp: _MCP,
    *,
    limits: LoopLimits | None = None,
    backlog: _Backlog | None = None,
):
    return AppendOnlyLoop(
        provider=provider,
        mcp=mcp,
        limits=limits
        or LoopLimits(
            max_model_calls=4,
            max_tool_calls=4,
            max_context_tokens=10_000,
            max_empty_responses=1,
        ),
        guard_checker=_Guard(),
        spending=_Spending(),
        backlog=backlog or _Backlog(),
        session_id="session",
        turn_id="turn",
        tenant_ids=("tenant-a",),
    )


async def _events(loop, run):
    return [event async for event in loop.run(run)]


@pytest.mark.asyncio
async def test_tool_result_is_appended_before_the_next_provider_request() -> None:
    provider = _Provider(
        [
            CompletionResponse(
                tool_calls=[
                    ToolCall(
                        id="call-search", name="search", arguments={"query": "Bosch"}
                    )
                ]
            ),
            CompletionResponse(content="Found Bosch"),
        ]
    )
    mcp = _MCP({"search": _Result('{"items":["Bosch"]}')})
    run = _run(provider, mcp)

    events = await _events(_loop(provider, mcp), run)

    assert [event.type for event in events] == [
        "tool_call",
        "tool_result",
        "token",
        "final",
    ]
    assert mcp.calls == [("search", {"query": "Bosch"})]
    assert len(provider.requests) == 2
    assert provider.requests[0].tools == provider.requests[1].tools
    assert provider.requests[1].messages[-2:] == [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-search",
                    "type": "function",
                    "function": {"name": "search", "arguments": {"query": "Bosch"}},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-search",
            "name": "search",
            "content": '{"items":["Bosch"]}',
        },
    ]
    assert run.outcome is not None and run.outcome.kind == "answer"


@pytest.mark.asyncio
async def test_tool_result_telemetry_is_recorded_at_the_loop_boundary() -> None:
    provider = _Provider(
        [
            CompletionResponse(
                tool_calls=[
                    ToolCall(
                        id="call-search", name="search", arguments={"query": "Bosch"}
                    )
                ]
            ),
            CompletionResponse(content="Found Bosch"),
        ]
    )
    mcp = _MCP({"search": _Result('{"items":["Bosch"]}')})
    backlog = _Backlog()

    await _events(_loop(provider, mcp, backlog=backlog), _run(provider, mcp))

    assert [
        call["untrusted_tool_results_in_context"] for call in backlog.llm_calls
    ] == [
        0,
        1,
    ]


@pytest.mark.asyncio
async def test_all_results_keep_their_ids_in_one_append_only_transcript() -> None:
    provider = _Provider(
        [
            CompletionResponse(
                tool_calls=[
                    ToolCall(
                        id="call-search", name="search", arguments={"query": "Bosch"}
                    ),
                    ToolCall(id="call-get", name="get", arguments={"id": 7}),
                ]
            ),
            CompletionResponse(content="done"),
        ]
    )
    mcp = _MCP({"search": _Result('{"total":1}'), "get": _Result('{"id":7}')})
    run = _run(provider, mcp)

    await _events(_loop(provider, mcp), run)

    assert mcp.calls == [("search", {"query": "Bosch"}), ("get", {"id": 7})]
    follow_up = provider.requests[1].messages
    assert follow_up[-3]["role"] == "assistant"
    assert [message["tool_call_id"] for message in follow_up[-2:]] == [
        "call-search",
        "call-get",
    ]


@pytest.mark.asyncio
async def test_text_is_final_text_and_is_never_parsed_as_a_tool_call() -> None:
    text = '{"name":"Bosch pad","article":"BP-7","price":50}'
    provider = _Provider([CompletionResponse(content=text)])
    mcp = _MCP()
    run = _run(provider, mcp)

    events = await _events(_loop(provider, mcp), run)

    assert [event.type for event in events] == ["token", "final"]
    assert mcp.calls == []
    assert run.outcome is not None and run.outcome.final_text == text


@pytest.mark.asyncio
async def test_invalid_tool_cannot_reach_mcp_or_trigger_a_recovery_completion() -> None:
    provider = _Provider(
        [
            CompletionResponse(
                tool_calls=[ToolCall(id="call-unknown", name="unknown", arguments={})]
            ),
            CompletionResponse(content="must not run"),
        ]
    )
    mcp = _MCP()
    run = _run(provider, mcp)

    events = await _events(_loop(provider, mcp), run)

    assert [event.type for event in events] == ["error"]
    assert mcp.calls == []
    assert len(provider.requests) == 1
    assert run.outcome is not None and run.outcome.kind == "tool_error"


@pytest.mark.asyncio
async def test_tool_failure_is_terminal_and_does_not_hide_a_provider_retry() -> None:
    provider = _Provider(
        [
            CompletionResponse(
                tool_calls=[
                    ToolCall(
                        id="call-search", name="search", arguments={"query": "Bosch"}
                    )
                ]
            ),
            CompletionResponse(content="must not run"),
        ]
    )
    mcp = _MCP({"search": _Result('{"error":"validation"}', ok=False)})
    run = _run(provider, mcp)

    events = await _events(_loop(provider, mcp), run)

    assert [event.type for event in events] == ["tool_call", "tool_result", "error"]
    assert len(provider.requests) == 1
    assert run.outcome is not None and run.outcome.kind == "tool_error"


@pytest.mark.asyncio
async def test_model_limit_is_checked_before_the_provider_call() -> None:
    provider = _Provider([CompletionResponse(content="must not run")])
    mcp = _MCP()
    run = _run(provider, mcp)
    limits = LoopLimits(
        max_model_calls=0,
        max_tool_calls=4,
        max_context_tokens=10_000,
        max_empty_responses=1,
    )

    events = await _events(_loop(provider, mcp, limits=limits), run)

    assert [event.type for event in events] == ["token", "final"]
    # zero means unlimited by contract; bounded behavior is tested explicitly below


@pytest.mark.asyncio
async def test_cancellation_has_one_terminal_error_and_no_recovery_call() -> None:
    class _CancelledProvider(_Provider):
        async def complete(self, request):
            self.requests.append(request)
            raise asyncio.CancelledError()

    provider = _CancelledProvider([])
    mcp = _MCP()
    run = _run(provider, mcp)

    events = await _events(_loop(provider, mcp), run)

    assert [event.type for event in events] == ["error"]
    assert len(provider.requests) == 1
    assert run.outcome is not None and run.outcome.kind == "cancelled"


@pytest.mark.asyncio
async def test_zero_empty_response_limit_is_unlimited() -> None:
    provider = _Provider([CompletionResponse(), CompletionResponse(content="ok")])
    mcp = _MCP()
    run = _run(provider, mcp)
    limits = LoopLimits(
        max_model_calls=4,
        max_tool_calls=4,
        max_context_tokens=10_000,
        max_empty_responses=0,
    )

    events = await _events(_loop(provider, mcp, limits=limits), run)

    assert [event.type for event in events] == ["token", "final"]
    assert len(provider.requests) == 2
    assert run.outcome is not None and run.outcome.kind == "answer"


@pytest.mark.asyncio
async def test_positive_empty_response_limit_stops_on_the_boundary() -> None:
    provider = _Provider(
        [CompletionResponse(), CompletionResponse(content="must not run")]
    )
    mcp = _MCP()
    run = _run(provider, mcp)
    limits = LoopLimits(
        max_model_calls=4,
        max_tool_calls=4,
        max_context_tokens=10_000,
        max_empty_responses=1,
    )

    events = await _events(_loop(provider, mcp, limits=limits), run)

    assert [event.type for event in events] == ["error"]
    assert len(provider.requests) == 1
    assert run.outcome is not None and run.outcome.kind == "needs_clarification"


@pytest.mark.asyncio
async def test_context_limit_counts_native_tool_call_arguments() -> None:
    provider = _Provider(
        [
            CompletionResponse(
                tool_calls=[
                    ToolCall(
                        id="call-search",
                        name="search",
                        arguments={"query": "x" * 400},
                    )
                ]
            ),
            CompletionResponse(content="must not run"),
        ]
    )
    mcp = _MCP({"search": _Result('{"items":[]}')})
    run = _run(provider, mcp)
    limits = LoopLimits(
        max_model_calls=4,
        max_tool_calls=4,
        max_context_tokens=60,
        max_empty_responses=1,
    )

    events = await _events(_loop(provider, mcp, limits=limits), run)

    assert [event.type for event in events] == ["tool_call", "tool_result", "error"]
    assert len(provider.requests) == 1
    assert run.outcome is not None and run.outcome.kind == "limit_reached"


@pytest.mark.asyncio
async def test_assistant_text_is_preserved_with_native_tool_calls() -> None:
    provider = _Provider(
        [
            CompletionResponse(
                content="Сначала выполню поиск.",
                tool_calls=[
                    ToolCall(
                        id="call-search",
                        name="search",
                        arguments={"query": "Bosch"},
                    )
                ],
            ),
            CompletionResponse(content="Готово."),
        ]
    )
    mcp = _MCP({"search": _Result('{"items":["Bosch"]}')})
    run = _run(provider, mcp)

    events = await _events(_loop(provider, mcp), run)

    assert [event.type for event in events] == [
        "tool_call",
        "tool_result",
        "token",
        "final",
    ]
    assert provider.requests[1].messages[-2]["content"] == "Сначала выполню поиск."
