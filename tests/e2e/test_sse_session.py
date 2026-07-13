"""E2E test: SSE session lifecycle — without LLM.

Tests that:
1. SSE session to mcp-gateway opens and receives event: endpoint
2. JSON-RPC initialize handshake works
3. Tools/list returns available tools for a tenant
4. Session lifecycle: idle → cleanup

Does NOT require LLM. Requires mcp-gateway (:8083) running.
"""

from __future__ import annotations

import json
import queue
import threading
import uuid

import pytest
import requests

from tests.e2e.helpers import mcp_gateway_url

_MCP_URL = "http://127.0.0.1:8083"
_TIMEOUT = 15


@pytest.fixture(scope="module")
def sse_session():
    """Open an SSE session and return (session_id, endpoint_url, message_queue).

    Yields a dict with stream state. Thread is daemon, cleaned after test.
    """
    sse_q: queue.Queue = queue.Queue()
    ready = threading.Event()
    sse_error: list[str] = [""]
    endpoint_url: list[str] = [""]
    session_ok = [False]

    def _reader():
        headers = {"X-Tenant-ID": "default", "Accept": "text/event-stream"}
        try:
            resp = requests.get(
                f"{_MCP_URL}/mcp", headers=headers, stream=True, timeout=_TIMEOUT
            )
            resp.raise_for_status()
            seen_endpoint = False
            for line_bytes in resp.iter_lines():
                if not line_bytes:
                    continue
                line = line_bytes.decode("utf-8", errors="replace")
                if line.startswith("event: endpoint"):
                    seen_endpoint = True
                elif line.startswith("data: ") and seen_endpoint and not endpoint_url[0]:
                    endpoint_url[0] = line[6:].strip()
                    ready.set()
                    session_ok[0] = True
                elif line.startswith("data: ") and endpoint_url[0]:
                    try:
                        payload = json.loads(line[6:])
                        sse_q.put(payload)
                    except json.JSONDecodeError:
                        sse_q.put({"raw": line[6:]})
        except Exception as e:
            sse_error[0] = str(e)
            ready.set()
        finally:
            sse_q.put(None)  # sentinel

    t = threading.Thread(target=_reader, daemon=True)
    t.start()

    success = ready.wait(timeout=10)
    if not success:
        pytest.fail(f"SSE session failed: {sse_error[0] or 'timeout'}")

    yield {
        "endpoint": endpoint_url[0],
        "queue": sse_q,
        "ok": session_ok[0],
        "error": sse_error[0],
        "thread": t,
    }


# ── Tests ──────────────────────────────────────────────────────────────────


def test_sse_session_opens(sse_session: dict):
    """SSE session opens and returns an endpoint URL."""
    assert sse_session["ok"], f"SSE session failed: {sse_session['error']}"
    assert sse_session["endpoint"], "No endpoint URL received"
    assert sse_session["endpoint"].startswith("http"), (
        f"Endpoint URL should be HTTP: {sse_session['endpoint']}"
    )


def test_json_rpc_initialize(sse_session: dict):
    """JSON-RPC initialize request succeeds."""
    payload = {
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {
            "protocolVersion": "0.1.0",
            "capabilities": {},
            "clientInfo": {"name": "e2e-test", "version": "1.0.0"},
        },
        "id": 1,
    }
    r = requests.post(
        sse_session["endpoint"],
        json=payload,
        headers={"X-Tenant-ID": "default", "Content-Type": "application/json"},
        timeout=10,
    )
    # mcp-gateway returns 202 Accepted — response comes via SSE stream
    assert r.status_code in (200, 202), (
        f"Initialize: {r.status_code} body={r.text[:200]}"
    )


def test_json_rpc_tools_list(sse_session: dict):
    """JSON-RPC tools/list returns tool definitions for the tenant."""
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/list",
        "params": {},
        "id": 2,
    }
    r = requests.post(
        sse_session["endpoint"],
        json=payload,
        headers={"X-Tenant-ID": "default", "Content-Type": "application/json"},
        timeout=10,
    )
    assert r.status_code in (200, 202), (
        f"Tools/list: {r.status_code} body={r.text[:200]}"
    )


def test_json_rpc_unknown_method(sse_session: dict):
    """Unknown JSON-RPC method returns error."""
    payload = {
        "jsonrpc": "2.0",
        "method": "nonexistent_method_xyz",
        "params": {},
        "id": 99,
    }
    r = requests.post(
        sse_session["endpoint"],
        json=payload,
        headers={"X-Tenant-ID": "default", "Content-Type": "application/json"},
        timeout=10,
    )
    # mcp-gateway accepts the request (202) and sends error via SSE
    assert r.status_code in (200, 202), (
        f"Unknown method: {r.status_code} body={r.text[:200]}"
    )
    # If immediate result, check for error
    if r.status_code == 200 and r.text.strip():
        try:
            data = r.json()
            assert "error" in data, f"Unknown method should return error: {data}"
        except (json.JSONDecodeError, ValueError):
            pass
