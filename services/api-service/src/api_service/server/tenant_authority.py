"""Authoritative tenant-scope resolution for public chat endpoints.

A browser request never chooses a tenant. Direct demo chat uses the single
server-configured tenant, while a named agent uses the scope persisted in its
Agent Store record. This module keeps that authorization boundary independent
from HTTP request parsing and MCP transport details.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from helperium_sdk.settings import settings


logger = logging.getLogger("api_service.server.tenant_authority")


@dataclass(frozen=True)
class DirectChatProfile:
    """Quality-only fields pinned onto direct chat by the operator.

    Deliberately has no tenant field: direct chat scope is owned by
    ``direct_chat_scope()`` and never by the pinned agent record.
    """

    llm_config: dict[str, Any] | None
    system_prompt: str | None
    provider_priority: list[str] | None


def direct_chat_scope() -> list[str]:
    """Return the sole server-configured tenant for direct demo chat."""
    return [settings.default_tenant_id]


def direct_chat_profile() -> DirectChatProfile | None:
    """Return the admin-managed direct-chat quality profile, if configured.

    Reads the agent record named by ``DIRECT_CHAT_AGENT`` from the Agent Store.
    Only presentation/quality fields (llm_config, system_prompt,
    provider_priority) are consumed; the record's persisted tenant scope is
    deliberately ignored so direct chat keeps its server-configured tenant
    boundary. Any failure degrades to the legacy pool/env provider resolution.
    """
    name = settings.direct_chat_agent
    if not name:
        return None
    try:
        # Imported lazily to avoid an import cycle with server.deps.
        from .deps import get_agent_store

        record = get_agent_store().get_agent(name)
    except Exception:
        logger.warning(
            "direct-chat profile lookup failed for agent %r; "
            "falling back to pool/env provider resolution",
            name,
            exc_info=True,
        )
        return None
    if not record:
        logger.warning(
            "DIRECT_CHAT_AGENT=%r has no agent record; "
            "falling back to pool/env provider resolution",
            name,
        )
        return None
    llm_config = record.get("llm_config")
    if not isinstance(llm_config, dict):
        llm_config = None
    raw_priority = record.get("provider_priority")
    provider_priority = (
        [p for p in raw_priority if isinstance(p, str)]
        if isinstance(raw_priority, list)
        else None
    )
    system_prompt = record.get("system_prompt")
    return DirectChatProfile(
        llm_config=llm_config,
        # Same prompt fallback chain as the named-agent route: top-level
        # system_prompt first, then the prompt embedded in llm_config.
        system_prompt=(system_prompt if isinstance(system_prompt, str) else None)
        or (llm_config.get("system_prompt") if llm_config else None),
        provider_priority=provider_priority or None,
    )


def named_agent_scope(agent: Mapping[str, object]) -> list[str] | None:
    """Return a named agent's persisted scope, never request-derived scope."""
    tenant_ids = agent.get("tenant_ids")
    if not isinstance(tenant_ids, list):
        return None
    scope = [tenant_id for tenant_id in tenant_ids if isinstance(tenant_id, str)]
    return scope or None
