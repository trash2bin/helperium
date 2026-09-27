"""Adapters for sync singletons (SpendingChecker, ModelBacklog) and for the
turn recorder the append-only loop depends on.

The loop talks to narrow ports (``agent.protocols``); this module decides
where an observed event actually goes. An LLM completion is one event with two
consumers — durable evidence in the backlog and the llm_* Prometheus series —
so it is composed here rather than emitted twice from the domain.
"""

from __future__ import annotations

import asyncio
from typing import Any

from helperium_sdk.settings import settings

from api_service.backlog import backlog
from api_service.prometheus_metrics import (
    llm_calls_total,
    llm_cost_total,
    llm_duration_ms,
    llm_token_usage,
)
from api_service.spending import (
    Reservation,
    get_spending_checker,
    get_spending_ledger,
    usd_to_micros,
)

from .protocols import TurnRecorderPort


__all__ = [
    "_AsyncSpendingTracker",
    "_BacklogRecorder",
    "_LedgerReservations",
    "_PrometheusLlmCallMetrics",
    "build_turn_recorder",
    "resolve_reservations",
]


class _AsyncSpendingTracker:
    """Async wrapper around the sync post-hoc SpendingChecker singleton."""

    async def record(self, tenant_id: str, cost: float) -> None:
        # SpendingChecker persists synchronously; keep filesystem I/O off the
        # event loop without changing its locking or post-hoc accounting model.
        await asyncio.to_thread(
            get_spending_checker().record_spending,
            tenant_id,
            cost,
        )

    async def check_limits(self, tenant_id: str) -> tuple[bool, str]:
        return get_spending_checker().check_limits(tenant_id)


class _LedgerReservations:
    """Async two-phase admission backed by the transactional ledger.

    Money crosses this boundary as USD floats (the provider/response unit) and
    is converted to integer micro-USD exactly once, here. Sub-cent turn costs
    are the norm, so cents would round every reservation and every commit to
    zero and silently disable admission.
    """

    async def reserve(
        self,
        principal_id: str,
        request_id: str,
        estimated_cost: float,
        tenant_ids: list[str],
    ) -> Reservation:
        return await asyncio.to_thread(
            get_spending_ledger().reserve,
            principal_id,
            request_id,
            usd_to_micros(estimated_cost),
            tenant_ids,
        )

    async def commit(self, request_id: str, actual_cost: float) -> None:
        await asyncio.to_thread(
            get_spending_ledger().commit,
            request_id,
            usd_to_micros(actual_cost),
        )

    async def release(self, request_id: str) -> None:
        await asyncio.to_thread(get_spending_ledger().release, request_id)


def resolve_reservations() -> _LedgerReservations | None:
    """Return the admission port only when the operator enabled reservations.

    The decision is a single explicit setting, never a test-shape heuristic:
    runtime and tests select the same code path.
    """
    if not settings.spending_reservations_enabled:
        return None
    return _LedgerReservations()


class _BacklogRecorder:
    """Durable evidence sink: the ModelBacklog singleton behind a narrow port.

    Deliberately synchronous. The write is an ``open``/``write``/``close`` per
    record on the event loop, and the per-session record order it produces is
    what the benchmark parser and the capacity evidence read. Moving that I/O
    off the loop is a separate decision with its own risks (ordering, and the
    documented concurrent-write hazard in ``test_backlog_concurrency``), not a
    side effect of this seam.
    """

    def turn_start(self, session_id: str, user_message: str) -> str:
        return backlog.turn_start(session_id, user_message)

    def turn_end(
        self,
        session_id: str,
        *,
        turn_id: str,
        duration_ms: float,
        outcome: str,
        total_prompt_tokens: int = 0,
        total_completion_tokens: int = 0,
        total_cost: float = 0.0,
        llm_calls: int = 0,
        tool_calls: int = 0,
        tool_errors: int = 0,
        empty_results: int = 0,
        empty_rounds: int = 0,
        iterations: int = 0,
        final_length_chars: int = 0,
        final_text: str = "",
        error_message: str = "",
    ) -> None:
        backlog.turn_end(
            session_id=session_id,
            turn_id=turn_id,
            duration_ms=duration_ms,
            outcome=outcome,
            total_prompt_tokens=total_prompt_tokens,
            total_completion_tokens=total_completion_tokens,
            total_cost=total_cost,
            llm_calls=llm_calls,
            tool_calls=tool_calls,
            tool_errors=tool_errors,
            empty_results=empty_results,
            empty_rounds=empty_rounds,
            iterations=iterations,
            final_length_chars=final_length_chars,
            final_text=final_text,
            error_message=error_message,
        )

    def record_llm_call(
        self,
        session_id: str,
        *,
        model: str,
        provider: str,
        duration_ms: float,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        cost: float,
        status: str,
        tenant_ids: list[str],
        turn_id: str,
        iteration: int,
        untrusted_tool_results_in_context: int,
    ) -> None:
        # Signature mirrors LoopRecorderPort on purpose. backlog.record_llm_call
        # accepts **extra and writes it into the durable record, so a loose
        # adapter would turn a misspelled field into silent evidence corruption
        # (the "tokens" field this seam replaced was exactly that).
        backlog.record_llm_call(
            session_id,
            model=model,
            provider=provider,
            duration_ms=duration_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost=cost,
            status=status,
            tenant_ids=tenant_ids,
            turn_id=turn_id,
            iteration=iteration,
            untrusted_tool_results_in_context=untrusted_tool_results_in_context,
        )

    def tool_call(
        self,
        session_id: str,
        turn_id: str,
        iteration: int,
        name: str,
        arguments: dict[str, Any],
    ) -> None:
        backlog.tool_call(session_id, turn_id, iteration, name, arguments)

    def tool_result(
        self,
        session_id: str,
        turn_id: str,
        iteration: int,
        name: str,
        result: str,
        duration_ms: float = 0.0,
    ) -> None:
        backlog.tool_result(session_id, turn_id, iteration, name, result, duration_ms)

    def error(
        self,
        session_id: str,
        turn_id: str,
        iteration: int,
        error: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        backlog.error(session_id, turn_id, iteration, error, context)


class _PrometheusLlmCallMetrics:
    """Telemetry for one logical provider completion.

    These series exist so that capacity/degradation is observable without
    parsing backlog files. They deliberately do not depend on ``BACKLOG_MODE``:
    a run with the backlog disabled must still report LLM latency, otherwise a
    discriminating run would also change what is being measured.
    """

    def record(
        self,
        *,
        model: str,
        provider: str,
        duration_ms: float,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        cost: float,
        tenant_ids: list[str],
    ) -> None:
        llm_calls_total.labels(model, provider).inc()
        llm_duration_ms.labels(model).observe(duration_ms)
        llm_token_usage.labels("prompt").inc(prompt_tokens)
        llm_token_usage.labels("completion").inc(completion_tokens)
        llm_token_usage.labels("total").inc(total_tokens)
        if cost > 0:
            # Same attribution rule as tenant spending: a composite-scope turn
            # reports its provider cost under every tenant in scope.
            for tenant_id in tenant_ids:
                llm_cost_total.labels(model, provider, tenant_id).inc(cost)


class _TurnRecorder:
    """One event, one call site: durable evidence plus per-call telemetry."""

    def __init__(
        self,
        *,
        backlog: _BacklogRecorder,
        metrics: _PrometheusLlmCallMetrics,
    ) -> None:
        self._backlog = backlog
        self._metrics = metrics

    def turn_start(self, session_id: str, user_message: str) -> str:
        return self._backlog.turn_start(session_id, user_message)

    def turn_end(
        self,
        session_id: str,
        *,
        turn_id: str,
        duration_ms: float,
        outcome: str,
        total_prompt_tokens: int = 0,
        total_completion_tokens: int = 0,
        total_cost: float = 0.0,
        llm_calls: int = 0,
        tool_calls: int = 0,
        tool_errors: int = 0,
        empty_results: int = 0,
        empty_rounds: int = 0,
        iterations: int = 0,
        final_length_chars: int = 0,
        final_text: str = "",
        error_message: str = "",
    ) -> None:
        self._backlog.turn_end(
            session_id,
            turn_id=turn_id,
            duration_ms=duration_ms,
            outcome=outcome,
            total_prompt_tokens=total_prompt_tokens,
            total_completion_tokens=total_completion_tokens,
            total_cost=total_cost,
            llm_calls=llm_calls,
            tool_calls=tool_calls,
            tool_errors=tool_errors,
            empty_results=empty_results,
            empty_rounds=empty_rounds,
            iterations=iterations,
            final_length_chars=final_length_chars,
            final_text=final_text,
            error_message=error_message,
        )

    def record_llm_call(
        self,
        session_id: str,
        *,
        model: str,
        provider: str,
        duration_ms: float,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        cost: float,
        status: str,
        tenant_ids: list[str],
        turn_id: str,
        iteration: int,
        untrusted_tool_results_in_context: int,
    ) -> None:
        self._backlog.record_llm_call(
            session_id,
            model=model,
            provider=provider,
            duration_ms=duration_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost=cost,
            status=status,
            tenant_ids=tenant_ids,
            turn_id=turn_id,
            iteration=iteration,
            untrusted_tool_results_in_context=untrusted_tool_results_in_context,
        )
        self._metrics.record(
            model=model,
            provider=provider,
            duration_ms=duration_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost=cost,
            tenant_ids=tenant_ids,
        )

    def tool_call(
        self,
        session_id: str,
        turn_id: str,
        iteration: int,
        name: str,
        arguments: dict[str, Any],
    ) -> None:
        self._backlog.tool_call(session_id, turn_id, iteration, name, arguments)

    def tool_result(
        self,
        session_id: str,
        turn_id: str,
        iteration: int,
        name: str,
        result: str,
        duration_ms: float = 0.0,
    ) -> None:
        self._backlog.tool_result(
            session_id, turn_id, iteration, name, result, duration_ms
        )

    def error(
        self,
        session_id: str,
        turn_id: str,
        iteration: int,
        error: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        self._backlog.error(session_id, turn_id, iteration, error, context)


def build_turn_recorder() -> TurnRecorderPort:
    """Wire the turn recorder for one turn: backlog evidence + telemetry."""
    return _TurnRecorder(
        backlog=_BacklogRecorder(),
        metrics=_PrometheusLlmCallMetrics(),
    )
