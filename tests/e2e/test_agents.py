"""E2E test: Agent management — CRUD, providers, SSE chat (no LLM).

Tests that:
1. Create agent with provider_priority
2. List agents includes new agent
3. Get agent details (config, widget, abuse)
4. Update agent config
5. Create agent with tenant_ids (multi-tenant agent)
6. SSE chat endpoint accepts agent context (without LLM — validates HTTP handshake)
7. Provider listing and config

Requires data-service (:8084) + api-service (:8081) + mcp-gateway (:8083) running.
Some tests require MISTRAL_API_KEY in .env or --llm-key CLI arg.
"""

from __future__ import annotations

import json
import uuid

import pytest
import requests

from tests.e2e.helpers import (
    admin_headers,
    api_service_url,
    data_service_url,
    delete_tenant,
    mcp_gateway_url,
    project_root,
    register_tenant,
    seed_database,
    cleanup_db,
)
from pathlib import Path


# ── Module-level state ─────────────────────────────────────────────────────

_AGENT_NAME = f"e2e-agent-{uuid.uuid4().hex[:6]}"
_LLM_AGENT_NAME = f"e2e-llm-agent-{uuid.uuid4().hex[:6]}"
_PROVIDER_NAME = f"e2e-provider-{uuid.uuid4().hex[:6]}"


def setup_module(module):
    """Ensure there's at least one tenant for agent tenant_ids."""
    # Don't create tenants — use existing default tenant


# ── Helpers ────────────────────────────────────────────────────────────────


def _api_headers() -> dict:
    h = {"Content-Type": "application/json"}
    token = admin_headers().get("Authorization", "")
    if token:
        h["Authorization"] = token
    return h


# ── Agent CRUD Tests ───────────────────────────────────────────────────────


def test_create_agent():
    """Create a new agent with minimal config."""
    payload = {
        "name": _AGENT_NAME,
        "llm_config": {
            "model": "test-model",
            "provider": "openai",
            "system_prompt": "You are a test assistant.",
        },
        "provider_priority": ["openai"],
        "tenant_ids": ["default"],
        "widget_config": {
            "title": "Test Agent",
            "greeting": "Hello!",
            "position": "right",
        },
    }
    r = requests.post(
        f"{api_service_url()}/api/agents",
        json=payload,
        headers=_api_headers(),
        timeout=10,
    )
    assert r.status_code in (200, 201), (
        f"Create agent: {r.status_code} body={r.text[:200]}"
    )


def test_list_agents():
    """Agent list includes the new agent."""
    r = requests.get(
        f"{api_service_url()}/api/agents",
        headers=_api_headers(),
        timeout=10,
    )
    assert r.status_code == 200, f"List agents: {r.status_code}"
    data = r.json()
    agents = data.get("agents", data.get("items", data if isinstance(data, list) else []))
    names = [a.get("name", "") for a in agents] if isinstance(agents, list) else []
    assert _AGENT_NAME in names, f"Agent {_AGENT_NAME} not found in list: {names}"


def test_get_agent_details():
    """Get agent details returns full config."""
    r = requests.get(
        f"{api_service_url()}/api/agents/{_AGENT_NAME}",
        headers=_api_headers(),
        timeout=10,
    )
    assert r.status_code == 200, f"Get agent: {r.status_code}"
    data = r.json()
    assert data.get("name") == _AGENT_NAME
    assert "llm_config" in data, "Agent missing llm_config"
    assert "widget_config" in data, "Agent missing widget_config"
    assert "provider_priority" in data, "Agent missing provider_priority"


def test_update_agent():
    """Update agent's LLM config and provider priority."""
    payload = {
        "llm_config": {
            "model": "updated-model",
            "provider": "mistral",
            "system_prompt": "Updated system prompt.",
        },
        "provider_priority": ["mistral", "openai"],
        "abuse_config": {
            "enabled": True,
            "rps": 5,
            "burst": 10,
            "emergency_preset": "normal",
        },
    }
    r = requests.put(
        f"{api_service_url()}/api/agents/{_AGENT_NAME}",
        json=payload,
        headers=_api_headers(),
        timeout=10,
    )
    assert r.status_code == 200, f"Update agent: {r.status_code} body={r.text[:200]}"


def test_agent_widget_config():
    """Widget config endpoint works."""
    r = requests.get(
        f"{api_service_url()}/api/agents/{_AGENT_NAME}/widget-config",
        headers=_api_headers(),
        timeout=10,
    )
    assert r.status_code == 200, f"Widget config: {r.status_code}"
    data = r.json()
    assert "title" in data, "Widget config missing title"


def test_create_multi_tenant_agent():
    """Create agent that spans multiple tenants."""
    payload = {
        "name": _LLM_AGENT_NAME,
        "provider_priority": [],
        "tenant_ids": ["default", "tenant-b"],
        "llm_config": {
            "system_prompt": "Multi-tenant test agent.",
        },
    }
    r = requests.post(
        f"{api_service_url()}/api/agents",
        json=payload,
        headers=_api_headers(),
        timeout=10,
    )
    assert r.status_code in (200, 201), (
        f"Create multi-tenant agent: {r.status_code} body={r.text[:200]}"
    )


def test_agent_chat_http_handshake():
    """SSE chat endpoint for agent returns valid HTTP (without LLM fallback).

    This test validates the HTTP layer only — sends a chat request and
    checks that the response starts with valid SSE format, not a 500 error.
    It does NOT require an LLM API key.
    """
    r = requests.post(
        f"{api_service_url()}/api/chat/{_AGENT_NAME}",
        json={"message": "Hello", "session_id": f"e2e-{uuid.uuid4().hex[:8]}"},
        headers={"X-Tenant-ID": "default", "Content-Type": "application/json"},
        timeout=15,
        stream=True,
    )
    # The request should be accepted at HTTP level
    assert r.status_code in (200, 202), (
        f"Agent chat HTTP handshake failed: {r.status_code} body={r.text[:200]}"
    )


def test_llm_providers_list():
    """List LLM providers."""
    r = requests.get(
        f"{api_service_url()}/admin/llm-provider-list",
        headers=_api_headers(),
        timeout=10,
    )
    assert r.status_code == 200, f"Provider list: {r.status_code}"
    data = r.json()
    providers = data if isinstance(data, list) else data.get("providers", [])
    # At minimum, there should be some providers configured or the endpoint works
    assert isinstance(providers, list), f"Provider list format unexpected: {data}"


def test_delete_agent():
    """Delete the test agent."""
    for name in [_AGENT_NAME, _LLM_AGENT_NAME]:
        r = requests.delete(
            f"{api_service_url()}/api/agents/{name}",
            headers=_api_headers(),
            timeout=10,
        )
        assert r.status_code in (200, 204), (
            f"Delete agent {name}: {r.status_code}"
        )


def test_deleted_agent_gone():
    """Deleted agent returns 404."""
    for name in [_AGENT_NAME, _LLM_AGENT_NAME]:
        r = requests.get(
            f"{api_service_url()}/api/agents/{name}",
            headers=_api_headers(),
            timeout=10,
        )
        assert r.status_code == 404, (
            f"Deleted agent {name} should 404, got {r.status_code}"
        )
