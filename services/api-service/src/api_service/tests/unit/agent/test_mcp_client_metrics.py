"""Observability regressions for the MCP client.

The MCP client is the most failure-prone boundary (SDK suppression, zombie
tasks, gateway outages); its failure escalation must be observable:
circuit-breaker trips, quarantines and reconnects need Prometheus counters
so operators can alert instead of grepping logs.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from api_service.agent.mcp_client import MCPClient, _TenantConnection
from api_service.prometheus_metrics import (
    mcp_circuit_breaker_trips_total,
    mcp_connection_quarantines_total,
    mcp_reconnects_total,
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
