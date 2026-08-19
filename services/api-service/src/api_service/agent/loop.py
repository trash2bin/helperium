"""Minimal append-only tool loop for one tenant-scoped agent turn."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from .models import CompletionRequest, CompletionResponse, ToolCall
from .protocols import LLMProvider, MCPToolSession
from .types import AgentEvent


class LoopLimits(BaseModel):
    """All execution limits for one loop; non-positive values mean no limit."""

    model_config = ConfigDict(frozen=True)

    max_model_calls: int
    max_tool_calls: int
    max_context_tokens: int
    max_empty_responses: int


class LoopOutcome(BaseModel):
    """The one explicit terminal result of a run."""

    model_config = ConfigDict(frozen=True)

    kind: Literal[
        "answer",
        "input_blocked",
        "provider_error",
        "tool_error",
        "dependency_unavailable",
        "limit_reached",
        "needs_clarification",
        "cancelled",
    ]
    message: str = ""
    final_text: str = ""
    retryable: bool = False


@dataclass
class Transcript:
    """The only context sent to the provider; messages are append-only."""

    messages: list[dict[str, Any]]
    current_turn_start: int

    @property
    def current_turn(self) -> list[dict[str, Any]]:
        return self.messages[self.current_turn_start :]

    def append(self, message: dict[str, Any]) -> None:
        self.messages.append(message)


@dataclass
class LoopMetrics:
    """Monotonic counters required for explicit limits and backlog bookkeeping."""

    model_calls: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    empty_responses: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_cost: float = 0.0


@dataclass
class LoopRun:
    """Small mutable shell around one transcript; no shadow context or event queue."""

    transcript: Transcript
    metrics: LoopMetrics = field(default_factory=LoopMetrics)
    outcome: LoopOutcome | None = None


class AppendOnlyLoop:
    """One bounded loop over native provider tool calls and scoped MCP tools.

    Each iteration sends the same transcript that received the preceding
    assistant tool-call message and the matching MCP tool-result messages.
    """

    def __init__(
        self,
        *,
        provider: LLMProvider,
        mcp: MCPToolSession,
        limits: LoopLimits,
        guard_checker: Any,
        spending: Any,
        backlog: Any,
        session_id: str,
        turn_id: str,
        tenant_ids: tuple[str, ...],
    ) -> None:
        self._provider = provider
        self._mcp = mcp
        self._limits = limits
        self._guard_checker = guard_checker
        self._spending = spending
        self._backlog = backlog
        self._session_id = session_id
        self._turn_id = turn_id
        self._tenant_ids = tenant_ids

    async def run(self, run: LoopRun) -> AsyncIterator[AgentEvent]:
        if self._input_blocked(run.transcript.messages[-1].get("content", "")):
            yield self._finish(
                run,
                LoopOutcome(
                    kind="input_blocked",
                    message="Ваше сообщение заблокировано системой безопасности.",
                ),
            )
            return

        try:
            tools = await self._mcp.list_tools()
        except Exception:
            yield self._finish(
                run,
                LoopOutcome(
                    kind="dependency_unavailable",
                    message="Сервис данных временно недоступен. Попробуйте ещё раз позже.",
                    retryable=True,
                ),
            )
            return
        allowed = _tool_index(tools)

        try:
            while True:
                limit = self._run_limit(run)
                if limit is not None:
                    yield self._finish(run, limit)
                    return

                run.metrics.model_calls += 1
                response = await self._provider.complete(
                    CompletionRequest(
                        messages=run.transcript.messages,
                        tools=self._tools_for_completion(run, tools),
                        tenant_ids=list(self._tenant_ids),
                    )
                )
                self._record_provider_response(run.metrics, response)
                spending = await self._check_spending(response.cost)
                if spending is not None:
                    yield self._finish(run, spending)
                    return

                if response.tool_calls:
                    run.transcript.append(
                        _assistant_tool_message(response.tool_calls, response.content)
                    )
                    async for event in self._run_tool_calls(
                        run, response.tool_calls, allowed
                    ):
                        yield event
                    if run.outcome is not None:
                        return
                    continue

                if response.content:
                    final_text = self._guard_output(response.content)
                    run.transcript.append({"role": "assistant", "content": final_text})
                    yield AgentEvent("token", {"data": final_text})
                    yield self._finish(
                        run, LoopOutcome(kind="answer", final_text=final_text)
                    )
                    return

                run.metrics.empty_responses += 1
                if self._empty_limit_reached(run.metrics):
                    yield self._finish(
                        run,
                        LoopOutcome(
                            kind="needs_clarification",
                            message="Не удалось получить содержательный ответ. Уточните запрос и попробуйте ещё раз.",
                        ),
                    )
                    return
        except asyncio.CancelledError:
            yield self._finish(
                run, LoopOutcome(kind="cancelled", message="Запрос отменён.")
            )
        except Exception:
            yield self._finish(
                run,
                LoopOutcome(
                    kind="provider_error",
                    message="Модель временно недоступна. Попробуйте ещё раз позже.",
                    retryable=True,
                ),
            )

    async def _run_tool_calls(
        self,
        run: LoopRun,
        calls: list[ToolCall],
        allowed: dict[str, dict[str, Any]],
    ) -> AsyncIterator[AgentEvent]:
        for call in calls:
            if self._tool_limit_reached(run.metrics):
                yield self._finish(
                    run,
                    LoopOutcome(
                        kind="limit_reached",
                        message="Достигнут лимит вызовов инструментов для этого запроса.",
                    ),
                )
                return
            validation_error = _validate_call(allowed.get(call.name), call.arguments)
            if validation_error:
                yield self._finish(
                    run, LoopOutcome(kind="tool_error", message=validation_error)
                )
                return

            run.metrics.tool_calls += 1
            self._backlog.tool_call(
                self._session_id,
                self._turn_id,
                run.metrics.model_calls,
                call.name,
                call.arguments,
            )
            yield AgentEvent(
                "tool_call",
                {"id": call.id, "name": call.name, "arguments": call.arguments},
            )
            started = time.monotonic()
            try:
                raw_result = await self._mcp.call_tool(call.name, call.arguments)
                content = raw_result.tool_content
                ok = bool(raw_result.ok)
            except asyncio.CancelledError:
                raise
            except Exception:
                content = json.dumps({"error": "tool invocation failed"})
                ok = False

            run.transcript.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.name,
                    "content": content,
                }
            )
            self._backlog.tool_result(
                self._session_id,
                self._turn_id,
                run.metrics.model_calls,
                call.name,
                content[:10_000],
                duration_ms=(time.monotonic() - started) * 1000,
            )
            yield AgentEvent(
                "tool_result",
                {
                    "id": call.id,
                    "name": call.name,
                    "result": content,
                    "isError": not ok,
                },
            )
            if not ok:
                run.metrics.tool_errors += 1
                dependency = _dependency_error(content)
                yield self._finish(
                    run,
                    LoopOutcome(
                        kind="dependency_unavailable" if dependency else "tool_error",
                        message=(
                            "Сервис данных временно недоступен. Попробуйте ещё раз позже."
                            if dependency
                            else "Не удалось выполнить запрос к данным."
                        ),
                        retryable=dependency,
                    ),
                )
                return

    def _tools_for_completion(
        self, run: LoopRun, tools: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Return schemas allowed for this provider at the current turn.

        Some providers accept native calls for the initial request but emit
        pseudo-tool text or fail when the full schema is resent after a tool
        result. This is an explicit provider capability, never a text parser.
        Existing providers default to the complete schema on every iteration.
        """
        if getattr(self._provider, "tools_after_tool_result", True):
            return tools
        if any(message.get("role") == "tool" for message in run.transcript.messages):
            return []
        return tools

    def _run_limit(self, run: LoopRun) -> LoopOutcome | None:
        if (
            self._limits.max_model_calls > 0
            and run.metrics.model_calls >= self._limits.max_model_calls
        ):
            return LoopOutcome(
                kind="limit_reached", message="Достигнут лимит шагов обработки запроса."
            )
        if self._limits.max_context_tokens > 0:
            transcript = json.dumps(
                run.transcript.messages,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            )
            if len(transcript) // 4 >= self._limits.max_context_tokens:
                return LoopOutcome(
                    kind="limit_reached",
                    message="Достигнут лимит контекста для этого запроса.",
                )
        return None

    def _record_provider_response(
        self, metrics: LoopMetrics, response: CompletionResponse
    ) -> None:
        usage = response.usage
        metrics.prompt_tokens += usage.prompt_tokens if usage else 0
        metrics.completion_tokens += usage.completion_tokens if usage else 0
        metrics.total_cost += response.cost
        self._backlog.record_llm_call(
            self._session_id,
            model=self._provider.model,
            provider=self._provider.model.split("/", 1)[0],
            duration_ms=0,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            total_tokens=usage.total_tokens if usage else 0,
            cost=response.cost,
            status="success",
            tenant_ids=list(self._tenant_ids),
            turn_id=self._turn_id,
            iteration=metrics.model_calls,
        )

    async def _check_spending(self, cost: float) -> LoopOutcome | None:
        if cost <= 0:
            return None
        for tenant_id in self._tenant_ids:
            await self._spending.record(tenant_id, cost)
        if len(self._tenant_ids) == 1:
            allowed, _reason = await self._spending.check_limits(self._tenant_ids[0])
            if not allowed:
                return LoopOutcome(
                    kind="limit_reached",
                    message="Лимит расходов исчерпан для этого тенанта.",
                )
        return None

    def _input_blocked(self, content: str) -> bool:
        return bool(
            self._guard_checker and self._guard_checker.check_input(content).blocked
        )

    def _guard_output(self, content: str) -> str:
        if self._guard_checker and self._guard_checker.check_output(content).blocked:
            return "[Ответ заблокирован системой безопасности]"
        return content

    def _tool_limit_reached(self, metrics: LoopMetrics) -> bool:
        return (
            self._limits.max_tool_calls > 0
            and metrics.tool_calls >= self._limits.max_tool_calls
        )

    def _empty_limit_reached(self, metrics: LoopMetrics) -> bool:
        return (
            self._limits.max_empty_responses > 0
            and metrics.empty_responses >= self._limits.max_empty_responses
        )

    @staticmethod
    def _finish(run: LoopRun, outcome: LoopOutcome) -> AgentEvent:
        if run.outcome is None:
            run.outcome = outcome
        if run.outcome.kind == "answer":
            return AgentEvent("final", {"content": run.outcome.final_text})
        return AgentEvent("error", {"message": run.outcome.message})


def _assistant_tool_message(calls: list[ToolCall], content: str = "") -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in calls
        ],
    }


def _tool_index(tools: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for tool in tools:
        function = tool.get("function") if isinstance(tool, dict) else None
        name = function.get("name") if isinstance(function, dict) else None
        if isinstance(name, str) and name:
            indexed[name] = tool
    return indexed


def _validate_call(
    tool: dict[str, Any] | None, arguments: dict[str, Any]
) -> str | None:
    if tool is None:
        return "Запрошенный инструмент недоступен для этого агента."
    schema = tool.get("function", {}).get("parameters", {})
    if not isinstance(schema, dict):
        return None
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        properties = {}
    required = schema.get("required", [])
    if isinstance(required, list) and any(name not in arguments for name in required):
        return "Для инструмента не хватает обязательных аргументов."
    if schema.get("additionalProperties") is False and set(arguments) - set(properties):
        return "Инструмент получил недопустимые аргументы."
    type_checks = {
        "string": lambda value: isinstance(value, str),
        "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
        "number": lambda value: (
            isinstance(value, (int, float)) and not isinstance(value, bool)
        ),
        "boolean": lambda value: isinstance(value, bool),
        "array": lambda value: isinstance(value, list),
        "object": lambda value: isinstance(value, dict),
    }
    for name, value in arguments.items():
        definition = properties.get(name)
        expected = definition.get("type") if isinstance(definition, dict) else None
        check = type_checks.get(expected) if isinstance(expected, str) else None
        if check and not check(value):
            return "Аргумент инструмента имеет недопустимый тип."
    return None


def _dependency_error(content: str) -> bool:
    lowered = content.lower()
    return any(
        marker in lowered
        for marker in (
            "dependency unavailable",
            "database unavailable",
            "service unavailable",
            "status 503",
            '"status":503',
        )
    )
