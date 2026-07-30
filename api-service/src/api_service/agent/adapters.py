"""Async adapters for sync singletons (SpendingChecker, ModelBacklog)."""

from __future__ import annotations

from typing import Any

from api_service.backlog import backlog
from api_service.spending import get_spending_checker


class _AsyncSpendingTracker:
    """Async wrapper around the sync SpendingChecker singleton."""

    async def record(self, tenant_id: str, cost: float) -> None:
        get_spending_checker().record_spending(tenant_id, cost)

    async def check_limits(self, tenant_id: str) -> tuple[bool, str]:
        return get_spending_checker().check_limits(tenant_id)


class _AsyncBacklogWriter:
    """Sync wrapper around the ModelBacklog singleton."""

    def record_llm_call(self, session_id: str, **kwargs: Any) -> None:
        backlog.record_llm_call(session_id, **kwargs)

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
