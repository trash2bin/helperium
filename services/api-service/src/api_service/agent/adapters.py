"""Async adapters for sync singletons (SpendingChecker, ModelBacklog)."""

from __future__ import annotations

import asyncio
from typing import Any

from helperium_sdk.settings import settings

from api_service.backlog import backlog
from api_service.spending import (
    Reservation,
    get_spending_checker,
    get_spending_ledger,
    usd_to_micros,
)


__all__ = [
    "_AsyncBacklogWriter",
    "_AsyncSpendingTracker",
    "_LedgerReservations",
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
