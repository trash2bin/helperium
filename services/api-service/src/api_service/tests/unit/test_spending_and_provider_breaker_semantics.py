"""Characterization and probe tests for two deferred-decision areas:

1. Legacy post-hoc spending semantics. These describe the path that runs when
   ``SPENDING_RESERVATIONS_ENABLED`` is off, which is still the default: the
   ``SpendingChecker`` accounts after the fact, the budget period is
   display-only, composite scopes are not admitted, and the JSON persistence
   writer previously had a shared temp-file race. Transactional admission is
   a separate service (``SQLiteSpendingLedger``); see ``test_spending_ledger``
   and ``agent/test_loop_spending_reservation`` for the enabled path.

2. Provider/LLM circuit breaker (queue item: "MCP breaker != provider/LLM
   breaker"): per-request bounded retries and per-turn failover exist, but
   there is no cross-request failure memory on the live fallback path.

Characterization tests assert the CURRENT behavior so that any future
reserve/commit or breaker work must consciously change them. Probe tests
target suspected defects; a probe failure means a real bug was found.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import threading
from pathlib import Path
from unittest.mock import AsyncMock


from api_service.agent.loop import AppendOnlyLoop, LoopLimits
from api_service.agent.models import CompletionRequest, CompletionResponse
from api_service.agent.provider_pool import FallbackProvider
from api_service.spending import SpendingChecker, SpendingConfig


# ── Spending: post-hoc accounting (no reserve) ──────────────────────────────


class TestSpendingIsPostHoc:
    """Cost is recorded after completion; nothing reserves budget up front."""

    def test_checker_has_no_admission_api(self):
        """Admission lives in SQLiteSpendingLedger, never in the checker.

        The checker stays a pure post-hoc accountant: its only gate is
        check_limits(), which compares already-recorded spend. If a reserve
        API ever appears here, two competing sources of truth exist.
        """
        checker = SpendingChecker(
            config=SpendingConfig(enabled=True, default_budget=10.0)
        )
        for missing in ("reserve", "try_reserve", "commit", "release", "admit"):
            assert not hasattr(checker, missing), (
                f"SpendingChecker gained '{missing}': admission must stay in "
                "SQLiteSpendingLedger, which owns transactional budget state."
            )

    def test_concurrent_records_overshoot_budget(self):
        """Post-hoc accounting cannot prevent an overrun.

        Parallel in-flight completions all record after finishing, so the
        tenant's realized spend overshoots the budget by design (N-1
        completions were admitted against a stale snapshot). This is exactly
        the gap the reserve/commit ledger closes when enabled."""
        checker = SpendingChecker(
            config=SpendingConfig(enabled=True, default_budget=10.0)
        )

        def burst():
            # Each "completion" finishes and records $1 concurrently; no
            # pre-flight admission exists, so all 50 land.
            checker.record_spending("tenant-race", 1.0)

        threads = [threading.Thread(target=burst) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert checker.get_spending("tenant-race")["total_cost"] == 50.0
        allowed, _ = checker.check_limits("tenant-race")
        assert allowed is False
        # The overrun already happened: 5x the budget was spent.

    def test_cost_is_recorded_even_when_check_would_block_it(self):
        """The just-completed call is always paid for: record happens BEFORE
        the limit check in AppendOnlyLoop._check_spending, and the check can
        only stop the NEXT call."""
        recorded: list[tuple[str, float]] = []

        class StubSpending:
            async def record(self, tenant_id: str, cost: float) -> None:
                recorded.append((tenant_id, cost))

            async def check_limits(self, tenant_id: str) -> tuple[bool, str]:
                return False, "Spending limit exceeded"

        loop = AppendOnlyLoop(
            provider=None,
            mcp=None,
            limits=LoopLimits(
                max_model_calls=1,
                max_tool_calls=1,
                max_context_tokens=1,
                max_empty_responses=1,
            ),
            guard_checker=None,
            spending=StubSpending(),
            reservations=None,
            backlog=None,
            session_id="s",
            turn_id="t",
            tenant_ids=("tenant-a",),
        )

        outcome = asyncio.run(loop._check_spending(0.5))

        assert recorded == [("tenant-a", 0.5)], (
            "the overrunning call was not even recorded"
        )
        assert outcome is not None and outcome.kind == "limit_reached"


# ── Spending: composite scope bypass ────────────────────────────────────────


class TestCompositeScopeSkipsAdmission:
    """_check_spending only enforces limits for single-tenant scopes."""

    def _loop(self, spending: object, tenants: tuple[str, ...]) -> AppendOnlyLoop:
        return AppendOnlyLoop(
            provider=None,
            mcp=None,
            limits=LoopLimits(
                max_model_calls=1,
                max_tool_calls=1,
                max_context_tokens=1,
                max_empty_responses=1,
            ),
            guard_checker=None,
            spending=spending,
            reservations=None,
            backlog=None,
            session_id="s",
            turn_id="t",
            tenant_ids=tenants,
        )

    def test_single_tenant_gets_limit_enforcement(self):
        stub = AsyncMock()
        stub.record = AsyncMock()
        stub.check_limits = AsyncMock(return_value=(False, "exceeded"))
        loop = self._loop(stub, ("tenant-a",))

        outcome = asyncio.run(loop._check_spending(1.0))

        stub.check_limits.assert_awaited_once_with("tenant-a")
        assert outcome is not None and outcome.kind == "limit_reached"

    def test_composite_scope_records_but_never_enforces(self):
        """Both tenants are billed, but NO admission check runs: a composite
        agent can drain either tenant's budget unchecked. The ledger path
        instead charges one principal once and keeps tenants as dimensions."""
        stub = AsyncMock()
        stub.record = AsyncMock()
        stub.check_limits = AsyncMock(return_value=(False, "exceeded"))
        loop = self._loop(stub, ("tenant-a", "tenant-b"))

        outcome = asyncio.run(loop._check_spending(1.0))

        assert stub.record.await_count == 2
        stub.check_limits.assert_not_awaited()
        assert outcome is None, "composite scope bypassed the limit"


# ── Spending: period is display-only, persistence writer race ───────────────


class TestSpendingPeriodAndPersistence:
    def test_period_never_resets_spending(self):
        """'monthly' is a label: no reset/prune job exists, so spend grows
        monotonically until the JSON file is deleted by hand."""
        checker = SpendingChecker(
            config=SpendingConfig(enabled=True, default_budget=10.0)
        )
        checker.record_spending("tenant-old", 100.0)
        assert checker.get_spending("tenant-old")["period"] == "monthly"
        for reset_api in ("reset", "reset_period", "prune", "rotate"):
            assert not hasattr(checker, reset_api), (
                f"SpendingChecker gained '{reset_api}': period semantics changed; "
                "update this characterization test."
            )
        assert checker.get_spending("tenant-old")["total_cost"] == 100.0

    def test_persistence_file_parses_after_concurrent_records(self):
        """PROBE (found a real defect): every writer used the same '<file>.tmp'
        name without holding the lock, so concurrent renames published TORN
        JSON documents (two half-documents spliced mid-file).

        Fixed contract: the file must always hold exactly ONE complete JSON
        document. Concurrent whole-snapshot writers are last-write-wins, so
        the final snapshot may briefly lag memory by a few entries; one more
        quiet record_spending must converge the file with memory."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "spending.json"
            checker = SpendingChecker(
                config=SpendingConfig(enabled=True, default_budget=10.0),
                persistence_path=path,
            )

            def burst(i: int) -> None:
                checker.record_spending(f"tenant-{i}", 1.0)

            threads = [threading.Thread(target=burst, args=(i,)) for i in range(30)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert path.exists()
            # The published file is a single complete document, never a
            # splice of two half-written snapshots.
            data = json.loads(path.read_text())
            assert set(data["spending"]) <= {f"tenant-{i}" for i in range(30)}
            assert all(v == 1.0 for v in data["spending"].values())

            # Convergence: memory is the source of truth; the next quiet
            # write publishes the full 30-tenant snapshot.
            checker.record_spending("tenant-flush", 0.0)
            data2 = json.loads(path.read_text())
            assert set(data2["spending"]) == {f"tenant-{i}" for i in range(30)} | {
                "tenant-flush"
            }


# ── Provider/LLM "circuit breaker": what exists and what does not ───────────


class _CountingProvider:
    """Minimal LLMProvider double that fails N times then succeeds."""

    def __init__(self, model: str, failures: int | None) -> None:
        """failures=None means the provider is permanently down (the state
        survives across FallbackProvider instances, simulating a new turn
        against the same dead upstream)."""
        self.model = model
        self.failures = failures
        self.attempts = 0

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.attempts += 1
        if self.failures is None or self.failures > 0:
            if self.failures is not None:
                self.failures -= 1
            raise RuntimeError(f"{self.model} is down")
        return CompletionResponse(content=f"answer from {self.model}")


class TestNoCrossRequestBreakerOnFallbackPath:
    def test_dead_primary_retried_by_every_new_instance(self):
        """Within one FallbackProvider instance a success sticks the active
        index, but resolve_llm builds a NEW instance per agent turn, so the
        dead primary is attempted again at the start of every turn: no
        cross-turn open state, only per-request bounded retries."""
        primary = _CountingProvider("primary/model", failures=None)
        backup = _CountingProvider("backup/model", failures=0)

        first = FallbackProvider([primary, backup])
        first_response = asyncio.run(first.complete(CompletionRequest(messages=[])))
        second = FallbackProvider([primary, backup])  # new turn, fresh instance
        second_response = asyncio.run(second.complete(CompletionRequest(messages=[])))

        assert first_response.content == "answer from backup/model"
        assert second_response.content == "answer from backup/model"
        # Both turns paid the dead-primary round trip.
        assert primary.attempts == 2
        assert backup.attempts == 2

    def test_success_within_one_instance_sticks(self):
        """Characterization of the per-turn stickiness that DOES exist."""
        primary = _CountingProvider("primary/model", failures=1)
        backup = _CountingProvider("backup/model", failures=0)
        provider = FallbackProvider([primary, backup])

        asyncio.run(provider.complete(CompletionRequest(messages=[])))
        asyncio.run(provider.complete(CompletionRequest(messages=[])))

        assert primary.attempts == 1, "active index should have moved to backup"
        assert provider.model == "backup/model"

    def test_failover_triggers_on_any_exception(self):
        """Characterization: failover is exception-type-blind; even a
        non-transient failure (e.g. bad auth) burns a fallback attempt."""

        class AuthFailed(_CountingProvider):
            async def complete(self, request: CompletionRequest) -> CompletionResponse:
                self.attempts += 1
                raise PermissionError("invalid api key")

        primary = AuthFailed("primary/model", failures=0)
        backup = _CountingProvider("backup/model", failures=0)
        provider = FallbackProvider([primary, backup])

        response = asyncio.run(provider.complete(CompletionRequest(messages=[])))

        assert primary.attempts == 1
        assert response.content == "answer from backup/model"


class TestMcpBreakerHasNoProviderEquivalent:
    def test_provider_pool_module_has_no_breaker_state_or_metric(self):
        """The MCP client keeps tenant-keyed breaker evidence and exports a
        trip counter; the provider layer has neither. Any open/closed state
        for LLM providers would be new code, not a config flip."""
        import api_service.agent.provider_pool as pool_module
        from api_service.agent.mcp_client import MCPClient

        assert hasattr(MCPClient(), "_breaker_state")
        from api_service.prometheus_metrics import mcp_circuit_breaker_trips_total

        assert mcp_circuit_breaker_trips_total is not None

        for breaker_artifact in ("_breaker_state", "_breaker_tripped_keys"):
            assert not hasattr(pool_module, breaker_artifact)
            assert not hasattr(pool_module.ProviderPool(), breaker_artifact)


# ── Sanity: the MCP breaker itself still works (already covered elsewhere,
#    kept here so this file can be read as a standalone gap report). ────────


class TestMcpBreakerBaseline:
    def test_mcp_breaker_opens_and_cools_down(self):
        from api_service.agent.mcp_client import (
            MCPClient,
            _CircuitBreakerOpen,
            _TenantConnection,
        )

        client = MCPClient()
        conn = _TenantConnection(tenant_id="tenant-x", session=None, session_ctx=None)
        for _ in range(3):  # default MCP_MAX_CONSECUTIVE_FAILURES=3
            client._mark_failure(conn)
        assert client._is_circuit_open(conn) is True
        assert _CircuitBreakerOpen is not None  # reconnects raise this while open
