"""Reserve/commit admission inside AppendOnlyLoop.

These tests drive the *enabled* admission path, including the flag resolution
and the real ledger adapter, so the production code path is covered rather than
only the legacy post-hoc branch.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from api_service.agent.loop import AppendOnlyLoop, LoopLimits, LoopRun, Transcript
from api_service.agent.messages import (
    MODEL_UNAVAILABLE,
    SPENDING_PRINCIPAL_LIMIT_REACHED,
)
from api_service.agent.models import CompletionResponse
from api_service.spending import BudgetExceeded, ReservationConflict

MODEL_PRICES = {
    "input_cost_per_token": 0.000002,
    "output_cost_per_token": 0.000004,
}


@dataclass
class _Reservation:
    request_id: str


class _Provider:
    model = "test/model"

    def __init__(self, events: list[str], cost: float = 0.60) -> None:
        self.events = events
        self.cost = cost
        self.calls = 0

    async def complete(self, _request: Any) -> CompletionResponse:
        self.events.append("provider")
        self.calls += 1
        return CompletionResponse(content="ok", cost=self.cost)


class _FailingProvider:
    model = "test/model"

    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def complete(self, _request: Any) -> CompletionResponse:
        self.events.append("provider")
        raise RuntimeError("upstream exploded")


class _MCP:
    async def list_tools(self) -> list[dict[str, Any]]:
        return []


class _Backlog:
    def record_llm_call(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def tool_call(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def tool_result(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _Spending:
    """Post-hoc accounting surface; stays alive for admin reporting."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.recorded: list[tuple[str, float]] = []
        self.limit_checks = 0

    async def record(self, tenant_id: str, cost: float) -> None:
        self.events.append("record")
        self.recorded.append((tenant_id, cost))

    async def check_limits(self, _tenant_id: str) -> tuple[bool, str]:
        self.limit_checks += 1
        return True, ""


class _Reservations:
    def __init__(self, events: list[str], *, reserve_error: Exception | None = None):
        self.events = events
        self.reservations: list[str] = []
        self.commits: list[tuple[str, float]] = []
        self.releases: list[str] = []
        self.estimates: list[float] = []
        self._reserve_error = reserve_error

    async def reserve(
        self,
        principal_id: str,
        request_id: str,
        estimated_cost: float,
        tenant_ids: list[str],
    ) -> _Reservation:
        self.events.append("reserve")
        assert principal_id == "agent-a"
        assert tenant_ids == ["tenant-a", "tenant-b"]
        if self._reserve_error is not None:
            raise self._reserve_error
        self.estimates.append(estimated_cost)
        self.reservations.append(request_id)
        return _Reservation(request_id)

    async def commit(self, request_id: str, actual_cost: float) -> None:
        self.events.append("commit")
        self.commits.append((request_id, actual_cost))

    async def release(self, request_id: str) -> None:
        self.events.append("release")
        self.releases.append(request_id)


def build_loop(
    *,
    provider: Any,
    spending: Any,
    reservations: Any,
    events: list[str],
    model_cost: dict[str, float] | None = None,
    max_model_calls: int = 1,
) -> AppendOnlyLoop:
    return AppendOnlyLoop(
        provider=provider,
        mcp=_MCP(),
        limits=LoopLimits(
            max_model_calls=max_model_calls,
            max_tool_calls=1,
            max_context_tokens=10_000,
            max_empty_responses=1,
        ),
        guard_checker=None,
        spending=spending,
        reservations=reservations,
        backlog=_Backlog(),
        session_id="session-1",
        turn_id="turn-1",
        tenant_ids=("tenant-a", "tenant-b"),
        principal_id="agent-a",
        model_cost=MODEL_PRICES if model_cost is None else model_cost,
        max_output_tokens=100,
    )


def new_run() -> LoopRun:
    return LoopRun(Transcript([{"role": "user", "content": "hello"}], 0))


# ── Happy path ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_loop_reserves_before_provider_and_commits_once() -> None:
    events: list[str] = []
    spending = _Spending(events)
    reservations = _Reservations(events)
    loop = build_loop(
        provider=_Provider(events),
        spending=spending,
        reservations=reservations,
        events=events,
    )

    events_out = [event async for event in loop.run(new_run())]

    assert events_out[-1].type == "final"
    assert events[:2] == ["reserve", "provider"]
    assert reservations.reservations == ["turn-1:model-1"]
    assert len(reservations.commits) == 1
    assert reservations.commits[0][1] == pytest.approx(0.60)
    assert reservations.releases == []


@pytest.mark.asyncio
async def test_admission_keeps_per_tenant_usage_visible() -> None:
    """Admission blocks, but the admin spending API must still see usage."""
    events: list[str] = []
    spending = _Spending(events)
    loop = build_loop(
        provider=_Provider(events),
        spending=spending,
        reservations=_Reservations(events),
        events=events,
    )

    events_out = [event async for event in loop.run(new_run())]

    assert events_out[-1].type == "final"
    assert spending.recorded == [("tenant-a", 0.60), ("tenant-b", 0.60)]
    # The post-hoc gate must not double-gate an already-admitted call.
    assert spending.limit_checks == 0


@pytest.mark.asyncio
async def test_reporting_failure_does_not_fail_a_paid_turn() -> None:
    events: list[str] = []

    class _BrokenSpending(_Spending):
        async def record(self, tenant_id: str, cost: float) -> None:
            raise RuntimeError("reporting store down")

    loop = build_loop(
        provider=_Provider(events),
        spending=_BrokenSpending(events),
        reservations=_Reservations(events),
        events=events,
    )

    events_out = [event async for event in loop.run(new_run())]

    assert events_out[-1].type == "final"


# ── Refusal and failure ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refused_reservation_is_a_spending_limit_not_an_internal_error() -> None:
    events: list[str] = []
    provider = _Provider(events)
    loop = build_loop(
        provider=provider,
        spending=_Spending(events),
        reservations=_Reservations(events, reserve_error=BudgetExceeded("no budget")),
        events=events,
    )

    events_out = [event async for event in loop.run(new_run())]

    assert events_out[-1].type == "error"
    assert events_out[-1].data["message"] == SPENDING_PRINCIPAL_LIMIT_REACHED
    # Refusal must happen before any money is spent.
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_reservation_conflict_is_reported_as_model_unavailable() -> None:
    events: list[str] = []
    loop = build_loop(
        provider=_Provider(events),
        spending=_Spending(events),
        reservations=_Reservations(
            events, reserve_error=ReservationConflict("duplicate")
        ),
        events=events,
    )

    events_out = [event async for event in loop.run(new_run())]

    assert events_out[-1].type == "error"
    assert events_out[-1].data["message"] == MODEL_UNAVAILABLE


@pytest.mark.asyncio
async def test_provider_failure_releases_the_reservation() -> None:
    events: list[str] = []
    reservations = _Reservations(events)
    loop = build_loop(
        provider=_FailingProvider(events),
        spending=_Spending(events),
        reservations=reservations,
        events=events,
    )

    events_out = [event async for event in loop.run(new_run())]

    assert events_out[-1].type == "error"
    assert reservations.releases == ["turn-1:model-1"]
    assert reservations.commits == []


@pytest.mark.asyncio
async def test_unknown_model_pricing_fails_closed_before_the_provider() -> None:
    events: list[str] = []
    provider = _Provider(events)
    loop = build_loop(
        provider=provider,
        spending=_Spending(events),
        reservations=_Reservations(events),
        events=events,
        model_cost={},
    )

    events_out = [event async for event in loop.run(new_run())]

    assert events_out[-1].type == "error"
    assert events_out[-1].data["message"] == MODEL_UNAVAILABLE
    assert provider.calls == 0


# ── Disabled path ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_without_reservations_the_post_hoc_path_is_used() -> None:
    events: list[str] = []
    spending = _Spending(events)
    loop = build_loop(
        provider=_Provider(events),
        spending=spending,
        reservations=None,
        events=events,
    )

    events_out = [event async for event in loop.run(new_run())]

    assert events_out[-1].type == "final"
    assert spending.recorded == [("tenant-a", 0.60), ("tenant-b", 0.60)]
    assert spending.limit_checks == 0  # composite scope is never gated
    assert "reserve" not in events


# ── Flag resolution and real ledger adapter ──────────────────────────────────


class TestFlagResolution:
    def test_reservations_are_off_by_default(self, monkeypatch) -> None:
        from helperium_sdk.settings import settings

        from api_service.agent.adapters import resolve_reservations

        monkeypatch.setattr(settings, "spending_reservations_enabled", False)
        assert resolve_reservations() is None

    def test_flag_is_the_only_switch(self, monkeypatch) -> None:
        """Selection must not depend on whether a provider was injected."""
        from helperium_sdk.settings import settings

        from api_service.agent.adapters import _LedgerReservations, resolve_reservations

        monkeypatch.setattr(settings, "spending_reservations_enabled", True)
        assert isinstance(resolve_reservations(), _LedgerReservations)


@pytest.mark.asyncio
async def test_enabled_loop_against_the_real_ledger(
    monkeypatch, tmp_path: Path
) -> None:
    """End-to-end admission through the flag, the adapter and SQLite."""
    from helperium_sdk.settings import settings

    from api_service.agent.adapters import resolve_reservations
    from api_service.spending import get_spending_ledger, reset_spending_singletons

    monkeypatch.setattr(settings, "spending_reservations_enabled", True)
    monkeypatch.setattr(
        settings, "spending_ledger_path", str(tmp_path / "ledger.sqlite3")
    )
    # $0.01 budget: a real turn costs a fraction of a cent, so this only holds
    # if money is tracked in micro-USD rather than cents.
    monkeypatch.setattr(settings, "spending_principal_default_budget", 0.01)
    reset_spending_singletons()

    events: list[str] = []
    reservations = resolve_reservations()
    assert reservations is not None
    loop = build_loop(
        provider=_Provider(events, cost=0.002),
        spending=_Spending(events),
        reservations=reservations,
        events=events,
    )

    events_out = [event async for event in loop.run(new_run())]

    assert events_out[-1].type == "final"
    balance = get_spending_ledger().balance("agent-a")
    assert balance.committed_micros == 2_000
    assert balance.reserved_micros == 0
    reset_spending_singletons()


@pytest.mark.asyncio
async def test_enabled_loop_is_refused_when_the_principal_budget_is_spent(
    monkeypatch, tmp_path: Path
) -> None:
    from helperium_sdk.settings import settings

    from api_service.agent.adapters import resolve_reservations
    from api_service.spending import get_spending_ledger, reset_spending_singletons

    monkeypatch.setattr(settings, "spending_reservations_enabled", True)
    monkeypatch.setattr(
        settings, "spending_ledger_path", str(tmp_path / "ledger.sqlite3")
    )
    monkeypatch.setattr(settings, "spending_principal_default_budget", 0.01)
    reset_spending_singletons()
    get_spending_ledger().reserve("agent-a", "already-held", 10_000, ["tenant-a"])

    events: list[str] = []
    provider = _Provider(events)
    reservations = resolve_reservations()
    assert reservations is not None
    loop = build_loop(
        provider=provider,
        spending=_Spending(events),
        reservations=reservations,
        events=events,
    )

    events_out = [event async for event in loop.run(new_run())]

    assert events_out[-1].type == "error"
    assert events_out[-1].data["message"] == SPENDING_PRINCIPAL_LIMIT_REACHED
    assert provider.calls == 0
    reset_spending_singletons()
