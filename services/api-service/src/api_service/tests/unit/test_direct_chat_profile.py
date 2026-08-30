"""Regressions for the admin-managed direct-chat quality profile.

``DIRECT_CHAT_AGENT`` lets an operator pin the system prompt and provider
selection for the public direct chat route without changing the tenant
authority boundary: the profile never contributes tenant scope.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from api_service.server.tenant_authority import (
    DirectChatProfile,
    direct_chat_profile,
    direct_chat_scope,
)


def _record() -> dict[str, object]:
    return {
        "name": "autoparts",
        "tenant_ids": ["some-other-tenant"],
        "llm_config": {
            "provider": "ollama",
            "model": "qwen2.5:0.5b",
            "system_prompt": "embedded prompt",
        },
        "system_prompt": "top-level prompt",
        "provider_priority": ["ollama"],
    }


def test_profile_unset_env_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Legacy deployments without DIRECT_CHAT_AGENT keep pool/env resolution."""
    monkeypatch.setattr(
        "api_service.server.tenant_authority.settings.direct_chat_agent", ""
    )
    assert direct_chat_profile() is None


def test_profile_reads_quality_fields_from_agent_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "api_service.server.tenant_authority.settings.direct_chat_agent",
        "autoparts",
    )
    store = MagicMock()
    store.get_agent.return_value = _record()
    with patch(
        "api_service.server.deps.get_agent_store", return_value=store
    ) as getter:
        getter.return_value = store
        profile = direct_chat_profile()

    assert profile == DirectChatProfile(
        llm_config={
            "provider": "ollama",
            "model": "qwen2.5:0.5b",
            "system_prompt": "embedded prompt",
        },
        system_prompt="top-level prompt",
        provider_priority=["ollama"],
    )
    store.get_agent.assert_called_once_with("autoparts")


def test_profile_system_prompt_falls_back_to_llm_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _record()
    record["system_prompt"] = None
    monkeypatch.setattr(
        "api_service.server.tenant_authority.settings.direct_chat_agent",
        "autoparts",
    )
    store = MagicMock()
    store.get_agent.return_value = record
    with patch(
        "api_service.server.deps.get_agent_store", return_value=store
    ) as getter:
        getter.return_value = store
        profile = direct_chat_profile()

    assert profile is not None
    assert profile.system_prompt == "embedded prompt"


def test_profile_missing_record_degrades_to_none(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(
        "api_service.server.tenant_authority.settings.direct_chat_agent",
        "ghost",
    )
    store = MagicMock()
    store.get_agent.return_value = None
    with patch(
        "api_service.server.deps.get_agent_store", return_value=store
    ) as getter:
        getter.return_value = store
        assert direct_chat_profile() is None


def test_profile_store_failure_degrades_to_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "api_service.server.tenant_authority.settings.direct_chat_agent",
        "autoparts",
    )
    with patch(
        "api_service.server.deps.get_agent_store",
        side_effect=RuntimeError("store unavailable"),
    ):
        assert direct_chat_profile() is None


def test_direct_chat_scope_untouched_by_profile_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The profile must never widen or replace the server-configured tenant."""
    monkeypatch.setattr(
        "api_service.server.tenant_authority.settings.direct_chat_agent",
        "autoparts",
    )
    monkeypatch.setattr(
        "api_service.server.tenant_authority.settings.default_tenant_id",
        "configured-demo-tenant",
    )
    store = MagicMock()
    store.get_agent.return_value = _record()
    with patch(
        "api_service.server.deps.get_agent_store", return_value=store
    ) as getter:
        getter.return_value = store
        assert direct_chat_scope() == ["configured-demo-tenant"]
