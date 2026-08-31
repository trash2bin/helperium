"""Minimal append-only tool loop for one tenant-scoped agent turn."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator

import litellm
from jsonschema import Draft202012Validator, SchemaError
from dataclasses import dataclass, field
from typing import Any, Literal
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict

from .messages import (
    CONTEXT_LIMIT,
    DATA_SERVICE_UNAVAILABLE,
    EMPTY_RESPONSE,
    INPUT_BLOCKED,
    MODEL_CALL_LIMIT,
    MODEL_UNAVAILABLE,
    MCP_TOOL_ERROR_NOTICE,
    REQUEST_CANCELLED,
    ARGUMENT_VALIDATION_NOTICE,
    SPENDING_LIMIT_REACHED,
    SPENDING_PRINCIPAL_LIMIT_REACHED,
    UNKNOWN_TOOL_NOTICE,
    TOOL_CALL_LIMIT,
    TOOL_INVALID_ARGUMENTS,
    TOOL_INVALID_ARGUMENT_TYPE,
    TOOL_INVOCATION_FAILED,
    TOOL_REQUIRED_ARGUMENTS,
    TOOL_UNAVAILABLE,
)
from .models import CompletionRequest, CompletionResponse, ToolCall
from .pricing import PricingConfigurationError, estimate_reservation_cost
from .protocols import LLMProvider, MCPToolSession, SpendingReservationPort
from .types import AgentEvent
from api_service.spending import BudgetExceeded, ReservationConflict


logger = logging.getLogger("api_service.agent.loop")

_RECOVERABLE_TOOL_ERROR_CODES = frozenset(
    {"ARGUMENT_VALIDATION_FAILED", "INVALID_RELATION"}
)
_DEPENDENCY_UNAVAILABLE_CODE = "DEPENDENCY_UNAVAILABLE"


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
        reservations: SpendingReservationPort | None = None,
        principal_id: str | None = None,
        model_cost: Mapping[str, Any] | None = None,
        max_output_tokens: int = 0,
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
        self._reservations = reservations
        self._principal_id = principal_id or (
            f"tenant:{tenant_ids[0]}" if len(tenant_ids) == 1 else "account:default"
        )
        self._model_cost = model_cost
        self._max_output_tokens = max_output_tokens

    async def run(self, run: LoopRun) -> AsyncIterator[AgentEvent]:
        if self._input_blocked(run.transcript.messages[-1].get("content", "")):
            yield self._finish(
                run,
                LoopOutcome(
                    kind="input_blocked",
                    message=INPUT_BLOCKED,
                ),
            )
            return

        try:
            tools = await self._mcp.list_tools()
        except Exception:
            logger.exception("[AGENT] failed to list tenant-scoped MCP tools")
            yield self._finish(
                run,
                LoopOutcome(
                    kind="dependency_unavailable",
                    message=DATA_SERVICE_UNAVAILABLE,
                    retryable=True,
                ),
            )
            return
        allowed = _tool_index(tools)

        try:
            while True:
                limit = self._run_limit(run, tools)
                if limit is not None:
                    yield self._finish(run, limit)
                    return

                run.metrics.model_calls += 1
                final_answer_only = self._is_final_model_call(run)
                untrusted_tool_results_in_context = (
                    self._untrusted_tool_results_in_context(run.transcript.messages)
                )
                logger.info(
                    "[AGENT] completion security iteration=%d "
                    "untrusted_tool_results_in_context=%d final_answer_only=%s",
                    run.metrics.model_calls,
                    untrusted_tool_results_in_context,
                    final_answer_only,
                )
                request_messages = run.transcript.messages
                request_tools = tools
                if final_answer_only:
                    request_tools = []
                # Stop the turn as soon as the HTTP client that owns it is
                # gone — do not spend another provider round-trip (thinking
                # tokens included) for a reader that already left.
                disconnect_check = getattr(self._mcp, "disconnect_check", None)
                if callable(disconnect_check) and disconnect_check():
                    logger.info(
                        "[AGENT] client disconnected; stopping turn before provider call"
                    )
                    yield self._finish(
                        run,
                        LoopOutcome(kind="cancelled", message=REQUEST_CANCELLED),
                    )
                    return
                reservation_id: str | None = None
                reservations = self._reservations
                if reservations is not None:
                    admission = await self._admit(
                        reservations, request_messages, run.metrics.model_calls
                    )
                    if isinstance(admission, LoopOutcome):
                        yield self._finish(run, admission)
                        return
                    reservation_id = admission
                try:
                    response = await self._provider.complete(
                        CompletionRequest(
                            messages=request_messages,
                            tools=request_tools,
                            tenant_ids=list(self._tenant_ids),
                        )
                    )
                except BaseException:
                    if reservation_id is not None and reservations is not None:
                        await reservations.release(reservation_id)
                    raise
                if reservation_id is not None and reservations is not None:
                    await reservations.commit(reservation_id, response.cost)
                    # Admission is authoritative for blocking, but per-tenant
                    # usage must stay visible to the admin spending API.
                    await self._record_tenant_spending(response.cost)
                self._record_provider_response(
                    run.metrics, response, untrusted_tool_results_in_context
                )
                if reservations is None:
                    spending = await self._check_spending(response.cost)
                    if spending is not None:
                        yield self._finish(run, spending)
                        return

                if response.tool_calls and not final_answer_only:
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

                if response.tool_calls and final_answer_only:
                    logger.warning(
                        "[AGENT] provider returned tool calls during final-only "
                        "iteration; ignoring %d call(s): %s",
                        len(response.tool_calls),
                        ", ".join(call.name for call in response.tool_calls),
                    )

                if response.content:
                    final_text = self._guard_output(response.content)
                    run.transcript.append({"role": "assistant", "content": final_text})
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
                            message=EMPTY_RESPONSE,
                        ),
                    )
                    return
        except asyncio.CancelledError:
            yield self._finish(
                run, LoopOutcome(kind="cancelled", message=REQUEST_CANCELLED)
            )
        except Exception:
            logger.exception("[AGENT] completion loop failed")
            yield self._finish(
                run,
                LoopOutcome(
                    kind="provider_error",
                    message=MODEL_UNAVAILABLE,
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
                        message=TOOL_CALL_LIMIT,
                    ),
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

            validation_error = _validate_call(allowed.get(call.name), call.arguments)
            if validation_error:
                # The model's native tool call is untrusted input. Do not send an
                # unknown tool or invalid arguments to MCP, but do give the model
                # the same tool-result turn it would receive for an MCP-side
                # validation error so it can correct the call within the loop
                # limits. Previously this branch terminated the whole turn before
                # the provider could recover.
                error_code = _local_tool_error_code(validation_error)
                content = json.dumps(
                    {
                        "ok": False,
                        "error": validation_error,
                        "error_code": error_code,
                    },
                    ensure_ascii=False,
                )
                run.metrics.tool_errors += 1
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
                    content,
                    duration_ms=0,
                )
                yield AgentEvent(
                    "tool_result",
                    {
                        "id": call.id,
                        "name": call.name,
                        "result": content,
                        "isError": True,
                    },
                )
                notice = (
                    UNKNOWN_TOOL_NOTICE
                    if error_code == "TOOL_NOT_FOUND"
                    else ARGUMENT_VALIDATION_NOTICE
                )
                run.transcript.append({"role": "system", "content": notice})
                continue

            started = time.monotonic()
            error_code: str | None = None
            try:
                raw_result = await self._mcp.call_tool(call.name, call.arguments)
                content = raw_result.tool_content
                ok = bool(raw_result.ok)
                error_code = getattr(raw_result, "error_code", None)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "[AGENT] MCP tool invocation failed tool=%s", call.name
                )
                content = json.dumps(
                    {"error_code": "TOOL_INVOCATION_FAILED"}, ensure_ascii=False
                )
                ok = False
                error_code = "TOOL_INVOCATION_FAILED"

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
                if error_code in _RECOVERABLE_TOOL_ERROR_CODES:
                    run.transcript.append(
                        {"role": "system", "content": MCP_TOOL_ERROR_NOTICE}
                    )
                    continue
                dependency = error_code == _DEPENDENCY_UNAVAILABLE_CODE
                yield self._finish(
                    run,
                    LoopOutcome(
                        kind="dependency_unavailable" if dependency else "tool_error",
                        message=(
                            DATA_SERVICE_UNAVAILABLE
                            if dependency
                            else TOOL_INVOCATION_FAILED
                        ),
                        retryable=dependency,
                    ),
                )
                return

    def _run_limit(
        self, run: LoopRun, tools: list[dict[str, Any]]
    ) -> LoopOutcome | None:
        if (
            self._limits.max_model_calls > 0
            and run.metrics.model_calls >= self._limits.max_model_calls
        ):
            return LoopOutcome(kind="limit_reached", message=MODEL_CALL_LIMIT)
        if self._limits.max_context_tokens > 0:
            estimated_tokens = self._context_token_count(run.transcript.messages, tools)
            if estimated_tokens >= self._limits.max_context_tokens:
                return LoopOutcome(
                    kind="limit_reached",
                    message=CONTEXT_LIMIT,
                )
        return None

    def _is_final_model_call(self, run: LoopRun) -> bool:
        """Whether this is the last permitted provider call for the turn.

        The boundary call is still counted inside ``max_model_calls``.  It is
        converted into a text-only request so a tool loop cannot consume the
        final slot and leave the user with a generic limit error.
        """
        return (
            self._limits.max_model_calls > 0
            and run.metrics.model_calls == self._limits.max_model_calls
        )

    def _context_token_count(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> int:
        """Estimate the full provider request, using LiteLLM before a safe fallback."""
        try:
            message_tokens = litellm.token_counter(
                model=self._provider.model,
                messages=messages,
            )
        except Exception:
            logger.warning(
                "[AGENT] LiteLLM token counter unavailable; using character fallback",
                exc_info=True,
            )
            transcript = json.dumps(
                messages,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            )
            message_tokens = len(transcript) // 4
        tool_schema = json.dumps(
            tools,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        return int(message_tokens) + len(tool_schema) // 4

    def _record_provider_response(
        self,
        metrics: LoopMetrics,
        response: CompletionResponse,
        untrusted_tool_results_in_context: int,
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
            untrusted_tool_results_in_context=untrusted_tool_results_in_context,
        )

    @staticmethod
    def _untrusted_tool_results_in_context(messages: list[dict[str, Any]]) -> int:
        """Count tool-result data structurally, independent of provider wire format."""
        return sum(message.get("role") == "tool" for message in messages)

    async def _admit(
        self,
        reservations: SpendingReservationPort,
        request_messages: list[dict[str, Any]],
        model_calls: int,
    ) -> str | LoopOutcome:
        """Reserve budget for one completion, or return the blocking outcome.

        Returns the reservation ID on admission. A refused reservation is a
        spending limit, not an internal failure, so it maps to the same
        ``limit_reached`` contract the post-hoc path uses.
        """
        try:
            estimate = estimate_reservation_cost(
                model=getattr(self._provider, "model", ""),
                messages=request_messages,
                max_output_tokens=self._max_output_tokens,
                model_cost=self._model_cost,
            )
        except PricingConfigurationError:
            logger.exception("[AGENT] missing safe pricing estimate")
            return LoopOutcome(
                kind="provider_error",
                message=MODEL_UNAVAILABLE,
                retryable=False,
            )
        reservation_id = f"{self._turn_id}:model-{model_calls}"
        try:
            await reservations.reserve(
                self._principal_id,
                reservation_id,
                estimate,
                list(self._tenant_ids),
            )
        except BudgetExceeded:
            logger.warning(
                "[AGENT] spending admission refused for %s", self._principal_id
            )
            return LoopOutcome(
                kind="limit_reached",
                message=SPENDING_PRINCIPAL_LIMIT_REACHED,
            )
        except ReservationConflict:
            logger.exception("[AGENT] reservation conflict for %s", reservation_id)
            return LoopOutcome(
                kind="provider_error",
                message=MODEL_UNAVAILABLE,
                retryable=False,
            )
        return reservation_id

    async def _record_tenant_spending(self, cost: float) -> None:
        """Keep per-tenant usage reporting alive under reserve/commit.

        Reporting must never fail an already-paid turn.
        """
        if cost <= 0:
            return
        for tenant_id in self._tenant_ids:
            try:
                await self._spending.record(tenant_id, cost)
            except Exception:
                logger.exception(
                    "[AGENT] failed to record tenant spending for %s", tenant_id
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
                    message=SPENDING_LIMIT_REACHED,
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
    """Validate native provider arguments against the advertised JSON Schema."""
    if tool is None:
        return TOOL_UNAVAILABLE
    schema = tool.get("function", {}).get("parameters", {})
    if not isinstance(schema, dict):
        return None
    try:
        validator = Draft202012Validator(schema)
    except SchemaError:
        logger.exception("[AGENT] invalid MCP tool JSON Schema")
        return TOOL_INVALID_ARGUMENTS
    errors = sorted(
        validator.iter_errors(arguments), key=lambda error: list(error.path)
    )
    if not errors:
        return None
    error = errors[0]
    if error.validator == "required":
        return TOOL_REQUIRED_ARGUMENTS
    if error.validator == "type":
        return TOOL_INVALID_ARGUMENT_TYPE
    return TOOL_INVALID_ARGUMENTS


def _local_tool_error_code(message: str) -> str:
    """Map a pre-dispatch validation failure to the shared recovery taxonomy."""
    if message == TOOL_UNAVAILABLE:
        return "TOOL_NOT_FOUND"
    if message == TOOL_REQUIRED_ARGUMENTS:
        return "ARGUMENT_VALIDATION_FAILED"
    if message in {TOOL_INVALID_ARGUMENT_TYPE, TOOL_INVALID_ARGUMENTS}:
        return "ARGUMENT_VALIDATION_FAILED"
    return "ARGUMENT_VALIDATION_FAILED"
