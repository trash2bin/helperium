"""Observability regressions for the MCP client.

The MCP client is the most failure-prone boundary (SDK suppression, zombie
tasks, gateway outages); its failure escalation must be observable:
circuit-breaker trips, quarantines and reconnects need Prometheus counters
so operators can alert instead of grepping logs.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prometheus_client import REGISTRY

from api_service.agent.mcp_client import MCPClient, _TenantConnection
from api_service.prometheus_metrics import (
    mcp_circuit_breaker_trips_total,
    mcp_connection_quarantines_total,
    mcp_lock_timeouts_total,
    mcp_lock_wait_seconds,
    mcp_reconnects_total,
    mcp_tool_timeouts_total,
)


@pytest.fixture
def mcp_client() -> MCPClient:
    return MCPClient()


def _conn(tenant: str) -> _TenantConnection:
    return _TenantConnection(
        tenant_id=tenant,
        session=MagicMock(),
        session_ctx=MagicMock(),
    )


def _counter_value(counter, labels: dict[str, str]) -> float:
    return counter.labels(**labels)._value.get()


def _histogram_count(histogram, tenant: str) -> float:
    name = f"{histogram._name}_count"
    return REGISTRY.get_sample_value(name, {"tenants": tenant}) or 0.0


def _SessionProxy(client: MCPClient, tenant_ids: list[str]):
    from api_service.agent.mcp_client import _SessionProxy as Proxy

    return Proxy(client, tenant_ids=tenant_ids)


class TestCircuitBreakerTripCounter:
    """One inc per closed→open transition, reset by success/reconnect.

    Prometheus counters are process-global, so every test uses its own
    tenant label and asserts on deltas, never absolute values.
    """

    def test_trip_counter_increments_once_at_threshold(self, mcp_client: MCPClient):
        from helperium_sdk.settings import settings

        tenant = "trip-once"
        threshold = settings.mcp_max_consecutive_failures
        before = _counter_value(mcp_circuit_breaker_trips_total, {"tenants": tenant})
        conn = _conn(tenant)
        for _ in range(threshold):
            mcp_client._mark_failure(conn)
        after = _counter_value(mcp_circuit_breaker_trips_total, {"tenants": tenant})
        assert after - before == 1

    def test_trip_not_repeated_while_breaker_stays_open(self, mcp_client: MCPClient):
        """Failures 4, 5, 6... must not re-fire the trip counter."""
        from helperium_sdk.settings import settings

        tenant = "trip-hold"
        threshold = settings.mcp_max_consecutive_failures
        before = _counter_value(mcp_circuit_breaker_trips_total, {"tenants": tenant})
        conn = _conn(tenant)
        for _ in range(threshold + 3):
            mcp_client._mark_failure(conn)
        after = _counter_value(mcp_circuit_breaker_trips_total, {"tenants": tenant})
        assert after - before == 1

    def test_success_resets_so_next_trip_counts_again(self, mcp_client: MCPClient):
        from helperium_sdk.settings import settings

        tenant = "trip-reset"
        threshold = settings.mcp_max_consecutive_failures
        before = _counter_value(mcp_circuit_breaker_trips_total, {"tenants": tenant})
        conn = _conn(tenant)
        for _ in range(threshold):
            mcp_client._mark_failure(conn)
        mcp_client._mark_success(conn)
        for _ in range(threshold):
            mcp_client._mark_failure(conn)
        after = _counter_value(mcp_circuit_breaker_trips_total, {"tenants": tenant})
        assert after - before == 2

    def test_untracked_failure_path_counts_trip(self, mcp_client: MCPClient):
        """Cold-handshake failures use _mark_failure_if_tracked(conn=None)."""
        from helperium_sdk.settings import settings

        tenant = "trip-cold"
        threshold = settings.mcp_max_consecutive_failures
        before = _counter_value(mcp_circuit_breaker_trips_total, {"tenants": tenant})
        for _ in range(threshold):
            mcp_client._mark_failure_if_tracked(conn=None, tenant_key=[tenant])
        after = _counter_value(mcp_circuit_breaker_trips_total, {"tenants": tenant})
        assert after - before == 1

    def test_counter_has_tenants_label(self):
        """Alerts key on {{ $labels.tenants }}; the label must exist."""
        sample = mcp_circuit_breaker_trips_total.labels(tenants="probe")
        assert sample._labelnames == ("tenants",)


class TestQuarantineAndReconnectLabels:
    """Quarantine/reconnect counters must carry the tenants label."""

    def test_quarantine_counter_has_tenants_label(self):
        sample = mcp_connection_quarantines_total.labels(tenants="probe")
        assert sample._labelnames == ("tenants",)

    def test_reconnect_counter_has_tenants_label(self):
        sample = mcp_reconnects_total.labels(tenants="probe")
        assert sample._labelnames == ("tenants",)

    @pytest.mark.asyncio
    async def test_reconnect_increments_tenant_label(
        self, mcp_client: MCPClient, monkeypatch
    ):
        tenant = "recon-labeled"

        async def fail_open(_self, _tenant_ids):
            raise ConnectionError("gateway down")

        monkeypatch.setattr(MCPClient, "_open_connection", fail_open)
        before = _counter_value(mcp_reconnects_total, {"tenants": tenant})
        with pytest.raises(ConnectionError):
            await mcp_client._reconnect([tenant])
        after = _counter_value(mcp_reconnects_total, {"tenants": tenant})
        assert after - before == 1

    @pytest.mark.asyncio
    async def test_reconnect_default_scope_uses_default_label(
        self, mcp_client: MCPClient, monkeypatch
    ):
        async def fail_open(_self, _tenant_ids):
            raise ConnectionError("gateway down")

        monkeypatch.setattr(MCPClient, "_open_connection", fail_open)
        before = _counter_value(mcp_reconnects_total, {"tenants": "(default)"})
        with pytest.raises(ConnectionError):
            await mcp_client._reconnect([])
        after = _counter_value(mcp_reconnects_total, {"tenants": "(default)"})
        assert after - before == 1


class TestLockWaitObservability:
    """Waiting on the per-tenant call lock must be measurable.

    One persistent Streamable HTTP connection (and therefore one call lock) is
    kept per tenant set, so concurrent turns for the same tenant serialize on
    that lock. That wait is the primary capacity hypothesis for the platform,
    yet it was invisible: only the execution timeout was counted and the
    lock-phase timeout produced the same public error text as a tool timeout,
    so lock starvation looked like a dependency outage. The wait and the
    lock-phase timeout are now observed, and the lock-phase timeout stays
    distinct from the execution-timeout (zombie escalation) counter.
    """

    def test_lock_metrics_carry_the_tenants_label(self):
        assert mcp_lock_wait_seconds._labelnames == ("tenants",)
        assert mcp_lock_timeouts_total.labels(tenants="probe")._labelnames == (
            "tenants",
        )

    @pytest.mark.asyncio
    @patch("helperium_sdk.settings.settings.mcp_lock_acquire_timeout", 5.0)
    @patch("helperium_sdk.settings.settings.mcp_tool_execution_timeout", 5.0)
    async def test_wait_for_a_held_lock_is_observed(self):
        tenant = "lock-wait-contended"
        client = MCPClient()
        lock = asyncio.Lock()
        await lock.acquire()
        conn = _conn(tenant)
        conn.session.call_tool = AsyncMock(
            return_value=MagicMock(
                content=[MagicMock(type="text", text='{"ok": true}')],
                is_error=False,
            )
        )
        client._get_connection = AsyncMock(return_value=conn)  # type: ignore[method-assign]

        before = _histogram_count(mcp_lock_wait_seconds, tenant)

        async def _release_after_wait() -> None:
            await asyncio.sleep(0.05)
            lock.release()

        releaser = asyncio.create_task(_release_after_wait())
        # The tool-call lock is session-scoped, so the contention to measure is
        # a second call inside the same session.
        session = _SessionProxy(client, [tenant])
        session.call_lock = lock
        try:
            result = await client.call_tool(session, "db_get", {"id": 1})
        finally:
            await releaser

        assert result.ok is True
        assert _histogram_count(mcp_lock_wait_seconds, tenant) == before + 1

    @pytest.mark.asyncio
    @patch("helperium_sdk.settings.settings.mcp_lock_acquire_timeout", 0.05)
    @patch("helperium_sdk.settings.settings.mcp_tool_execution_timeout", 5.0)
    async def test_lock_phase_timeout_is_not_counted_as_an_execution_timeout(self):
        tenant = "lock-timeout-separate"
        client = MCPClient()
        lock = asyncio.Lock()
        await lock.acquire()
        conn = _conn(tenant)
        conn.session.call_tool = AsyncMock()
        client._get_connection = AsyncMock(return_value=conn)  # type: ignore[method-assign]

        lock_before = _counter_value(mcp_lock_timeouts_total, {"tenants": tenant})
        exec_before = _counter_value(mcp_tool_timeouts_total, {"tenants": tenant})
        wait_before = _histogram_count(mcp_lock_wait_seconds, tenant)

        session = _SessionProxy(client, [tenant])
        session.call_lock = lock
        result = await client.call_tool(session, "db_get", {"id": 1})

        assert result.ok is False
        assert (
            _counter_value(mcp_lock_timeouts_total, {"tenants": tenant})
            == lock_before + 1
        )
        assert (
            _counter_value(mcp_tool_timeouts_total, {"tenants": tenant}) == exec_before
        )
        assert _histogram_count(mcp_lock_wait_seconds, tenant) == wait_before + 1
        conn.session.call_tool.assert_not_awaited()
