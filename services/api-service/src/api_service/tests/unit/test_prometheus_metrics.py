"""Prometheus metric contracts: histogram resolution and multi-worker aggregation."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from prometheus_client import CONTENT_TYPE_LATEST

import api_service.prometheus_metrics as metrics
from api_service.prometheus_metrics import llm_duration_ms, mcp_lock_wait_seconds
from api_service.server.app import metrics_endpoint


def test_llm_histogram_resolves_the_latency_range_seen_in_stress_runs():
    """Values are 27-45ms in stress runs; the old buckets started at 500ms."""
    bounds = list(llm_duration_ms._upper_bounds)
    for fine in (
        5.0,
        10.0,
        15.0,
        20.0,
        25.0,
        30.0,
        35.0,
        40.0,
        45.0,
        50.0,
        75.0,
        100.0,
    ):
        assert fine in bounds, f"missing sub-500ms bucket {fine}"
    # Old boundaries stay: dashboards and alerts query sum/count, not buckets.
    for old in (500.0, 1000.0, 120000.0):
        assert old in bounds


def test_mcp_lock_histogram_resolves_sub_millisecond_waits():
    bounds = list(mcp_lock_wait_seconds._upper_bounds)
    for fine in (1e-6, 5e-6, 1e-5, 2.5e-5, 5e-5, 1e-4, 2.5e-4, 5e-4):
        assert fine in bounds, f"missing sub-ms bucket {fine}"
    for old in (0.001, 0.01, 1.0, 10.0):
        assert old in bounds


def _spawn_metric_writer(tmp_path: Path, script: str) -> None:
    """Write metrics from a separate process, like a uvicorn worker does."""
    subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        env={**os.environ, "PROMETHEUS_MULTIPROC_DIR": str(tmp_path)},
        capture_output=True,
        text=True,
    )


def test_render_metrics_aggregates_counters_and_histograms_across_workers(
    tmp_path: Path, monkeypatch
):
    for increment, observation in ((2, 0.005), (3, 0.5)):
        _spawn_metric_writer(
            tmp_path,
            "from prometheus_client import Counter, Histogram\n"
            "Counter('worker_probe_requests_total', 'probe').inc("
            + str(increment)
            + ")\n"
            "Histogram('worker_probe_latency_seconds', 'probe',"
            " buckets=(0.001, 0.01, 1)).observe(" + str(observation) + ")\n",
        )

    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))
    exposition = metrics.render_metrics().decode()
    assert "worker_probe_requests_total 5.0" in exposition
    assert "worker_probe_latency_seconds_count 2.0" in exposition
    assert 'worker_probe_latency_seconds_bucket{le="0.01"} 1.0' in exposition
    assert 'worker_probe_latency_seconds_bucket{le="1.0"} 2.0' in exposition


@pytest.mark.asyncio
async def test_metrics_endpoint_aggregates_every_worker(tmp_path, monkeypatch):
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))
    _spawn_metric_writer(
        tmp_path,
        "from prometheus_client import Counter\n"
        "Counter('endpoint_probe_total', 'probe').inc(7)\n",
    )
    response = await metrics_endpoint()
    assert response.media_type == CONTENT_TYPE_LATEST
    assert b"endpoint_probe_total 7.0" in response.body


def test_single_process_metrics_keep_the_default_registry(monkeypatch):
    monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
    llm_duration_ms.labels("probe-single-worker").observe(23)
    rendered = metrics.render_metrics()
    assert (
        b'llm_duration_ms_bucket{le="25.0",model="probe-single-worker"} 1.0' in rendered
    )
    assert b"worker_probe_requests_total" not in rendered
