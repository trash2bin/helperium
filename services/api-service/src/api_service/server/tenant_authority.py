"""Authoritative tenant-scope resolution for public chat endpoints.

A browser request never chooses a tenant. Direct demo chat uses the single
server-configured tenant, while a named agent uses the scope persisted in its
Agent Store record. This module keeps that authorization boundary independent
from HTTP request parsing and MCP transport details.
"""

from __future__ import annotations

from collections.abc import Mapping

from helperium_sdk.settings import settings


def direct_chat_scope() -> list[str]:
    """Return the sole server-configured tenant for direct demo chat."""
    return [settings.default_tenant_id]


def named_agent_scope(agent: Mapping[str, object]) -> list[str] | None:
    """Return a named agent's persisted scope, never request-derived scope."""
    tenant_ids = agent.get("tenant_ids")
    if not isinstance(tenant_ids, list):
        return None
    scope = [tenant_id for tenant_id in tenant_ids if isinstance(tenant_id, str)]
    return scope or None
