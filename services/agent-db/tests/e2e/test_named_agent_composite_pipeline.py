"""End-to-end named-agent composite tenant pipeline on Streamable HTTP v2.

This test covers the complete authority and data path:

persisted Agent Store tenant_ids -> api-service -> MCPClient -> mcp-gateway
-> prefixed composite tool -> data-service -> browser SSE tool result.

The client sends a hostile X-Tenant-ID header deliberately. It must not affect
the persisted named-agent scope.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import requests

from tests.e2e.helpers import (
    admin_headers,
    find_free_port,
    make_tenant,
    parse_sse_stream,
    project_root,
    wait_for_health,
)


pytestmark = pytest.mark.skipif(
    not admin_headers(),
    reason="ADMIN_TOKEN not set",
)


def _write_composite_script(
    path: Path, first_tool: str, second_tool: str
) -> None:
    """Write one dialogue that reads from two different tenant databases."""
    rounds = [
        {
            "content": "Сначала проверю схему первого тенанта.",
            "tool_calls": [{"name": first_tool, "arguments": {}}],
        },
        {
            "content": "Теперь найду запчасть во втором тенанте.",
            "tool_calls": [
                {
                    "name": second_tool,
                    "arguments": {
                        "entity": "auto_parts",
                        "pattern": "глушитель",
                        "limit": 3,
                    },
                }
            ],
        },
        {"content": "Composite MCP pipeline completed."},
    ]
    path.write_text(
        "".join(json.dumps(round, ensure_ascii=False) + "\n" for round in rounds),
        encoding="utf-8",
    )


@pytest.fixture
def named_composite_scripted_api(tmp_path):
    """Create two tenants and an isolated ScriptedLLM API process."""
    first = make_tenant("sqlite-testseed", prefix="named-composite-first").register()
    second = make_tenant("auto-shop", prefix="named-composite-second").register()
    expected_tools = [f"{first.id}__db_map", f"{second.id}__db_search"]

    script_path = tmp_path / "composite.jsonl"
    _write_composite_script(script_path, *expected_tools)
    port = find_free_port()
    api_url = f"http://127.0.0.1:{port}"
    agent_name = f"e2e-named-composite-{uuid.uuid4().hex[:8]}"
    root = project_root()
    log_path = tmp_path / "api.log"
    log_file = log_path.open("w", buffering=1)

    env = os.environ.copy()
    env.update(
        {
            "USE_SCRIPTED_LLM": "1",
            "SCRIPTED_LLM_PATH": str(script_path),
            "ADMIN_TOKEN": os.environ.get("ADMIN_TOKEN", "secret"),
            "API_BEARER_TOKEN": os.environ.get("API_BEARER_TOKEN", "api-secret"),
            "MCP_GATEWAY_URL": os.environ.get(
                "MCP_GATEWAY_URL", "http://127.0.0.1:8083"
            ),
            "DATA_SERVICE_URL": os.environ.get(
                "DATA_SERVICE_URL", "http://127.0.0.1:8084"
            ),
            "DEMO_SESSION_DB_PATH": str(tmp_path / "sessions.db"),
            "AGENT_DB_PATH": str(tmp_path / "agents.db"),
            "LOG_LEVEL": "info",
        }
    )
    env.setdefault(
        "MCP_STREAMABLE_HTTP_URL", env["MCP_GATEWAY_URL"].rstrip("/") + "/mcp"
    )

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "api_service.server:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "info",
        ],
        cwd=str(root / "services/api-service/src"),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )

    try:
        if not wait_for_health(api_url, timeout=30):
            log_file.flush()
            pytest.fail(
                "Scripted composite api-service failed to start:\n"
                + log_path.read_text(encoding="utf-8")[-3000:]
            )

        response = requests.post(
            f"{api_url}/api/agents",
            json={
                "name": agent_name,
                "tenant_ids": [first.id, second.id],
                "llm_config": {
                    "model": "scripted/test",
                    "provider": "openai",
                    "api_key": "test-key",
                },
            },
            headers={"Authorization": f"Bearer {env['API_BEARER_TOKEN']}"},
            timeout=10,
        )
        assert response.status_code in (200, 201), response.text
        yield api_url, agent_name, expected_tools
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        log_file.close()
        first.cleanup()
        second.cleanup()


def test_named_agent_composite_scope_reaches_prefixed_mcp_tool(
    named_composite_scripted_api,
):
    """Persisted composite scope reaches real prefixed tools end-to-end."""
    api_url, agent_name, expected_tools = named_composite_scripted_api
    response = requests.post(
        f"{api_url}/api/chat/{agent_name}",
        json={"message": "Найди глушитель", "session_id": "composite-pipeline"},
        headers={
            "Content-Type": "application/json",
            "User-Agent": "HelperiumE2E/1.0",
            "X-Tenant-ID": "attacker-controlled-tenant",
        },
        timeout=60,
        stream=True,
    )
    assert response.status_code == 200, response.text
    result = parse_sse_stream(response, idle_timeout=20)

    assert not result["errors"], result["errors"]
    assert [event["name"] for event in result["tool_calls"]] == expected_tools
    assert [event["name"] for event in result["tool_results"]] == expected_tools

    first_result = json.dumps(result["tool_results"][0], ensure_ascii=False)
    second_result = json.dumps(result["tool_results"][1], ensure_ascii=False)
    assert "student" in first_result.lower(), first_result
    assert "глушитель" in second_result.lower(), second_result
    assert result["final_text"].endswith("Composite MCP pipeline completed.")
